import asyncio
from collections import deque
import copy
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from overlay.client import Client
from overlay.secure import TransportIdentity, PeerLink, verify_binding, MAX_RELIABLE_PAYLOAD
from test_identity import identities


def pair(members, *, start=True):
    clients = []
    queue = deque()
    class Transport:
        def __init__(self, source, target):
            self.source, self.target = source, target
            self.count, self.loss = 0, 0
            self.maximum_packet = 0
        def sendto(self, data, destination):
            self.maximum_packet = max(self.maximum_packet, len(data))
            self.count += 1
            if self.loss and self.count % self.loss == 0:
                return
            queue.append((self.target, data, self.source))
    for node, peer, port, other_port in (("a", "b", 1001, 1002), ("b", "a", 1002, 1001)):
        client = Client({"server": "127.0.0.1"}, node, peer, "direct-only")
        client.allocation_id, client.sid = "ee" * 16, node * 32
        client.relay_side = 0 if node == "a" else 1
        client.credentials = TransportIdentity(members[node]["identity"])
        client.binding = client.credentials.bind(node, peer, client.sid)
        client.peer_identity = members[peer]["public_key"]
        client.peer_endpoint = ("127.0.0.1", other_port)
        clients.append(client)
    a, b = clients
    a.peer_binding, b.peer_binding = b.binding, a.binding
    a.transport = Transport(("127.0.0.1", 1001), b)
    b.transport = Transport(("127.0.0.1", 1002), a)
    if start:
        a.link = PeerLink(a, "direct")
    return a, b, queue


async def pump(a, b, queue, predicate, timeout=5):
    async with asyncio.timeout(timeout):
        while not predicate():
            while queue:
                owner, data, source = queue.popleft()
                owner.datagram_received(data, source)
            now = time.perf_counter()
            for client in (a, b):
                if client.link:
                    client.link.tick(now)
                client.select_path()
            await asyncio.sleep(0.002)


def test_certificate_binding_rejects_tampering_and_wrong_identity(identities):
    _, members = identities
    identity = TransportIdentity(members["a"]["identity"])
    binding = identity.bind("a", "b", "aa" * 16)
    verify_binding(binding, members["a"]["public_key"], "a", "b")
    changed = copy.deepcopy(binding)
    changed["body"]["sid"] = "bb" * 16
    with pytest.raises(ValueError):
        verify_binding(changed, members["a"]["public_key"], "a", "b")
    with pytest.raises(ValueError):
        verify_binding(binding, members["b"]["public_key"], "a", "b")


def test_quic_reliable_stream_recovers_loss_without_duplicate_delivery(identities):
    async def run():
        _, members = identities
        a, b, queue = pair(members)
        delivered = []
        b.on_data = lambda data, path: delivered.append(data)
        await pump(a, b, queue, lambda: a.ready.is_set() and b.ready.is_set())
        a.transport.loss = b.transport.loss = 3
        for i in range(20):
            assert a.send(i.to_bytes(8, "big") + bytes(892))
        await pump(a, b, queue, lambda: len(delivered) == 20 and not a.pending_count)
        assert [int.from_bytes(data[:8], "big") for data in delivered] == list(range(20))
    asyncio.run(run())


def test_unreliable_datagrams_share_authenticated_connection(identities):
    async def run():
        _, members = identities
        a, b, queue = pair(members)
        received = []
        b.on_data = lambda data, path: received.append(data)
        await pump(a, b, queue, lambda: a.ready.is_set() and b.ready.is_set())
        assert a.send(b"datagram", reliable=False)
        assert not a.pending_count
        await pump(a, b, queue, lambda: bool(received))
        assert received == [b"datagram"]
        with pytest.raises(ValueError):
            a.send(bytes(901), reliable=False)
        with pytest.raises(ValueError):
            a.send(bytes(MAX_RELIABLE_PAYLOAD + 1))
    asyncio.run(run())


def test_tls_completion_without_client_identity_proof_cannot_authorize_data(identities):
    async def run():
        a, b, queue = pair(identities[1])
        a.credentials.identity = Ed25519PrivateKey.generate()
        await pump(a, b, queue, lambda: b.link and b.link.closed)
        assert b.link.tls_complete and not b.link.authenticated
        assert not a.ready.is_set() and not a.send(b'forbidden')
    asyncio.run(run())


def test_invalid_peer_certificate_cannot_establish_application_link(identities):
    async def run():
        _, members = identities
        a, b, queue = pair(members)
        # 签名绑定指向的证书与服务端实际使用的私钥/证书不一致。
        b.credentials = TransportIdentity(members["b"]["identity"])
        await pump(a, b, queue, lambda: a.link.closed)
        assert not a.link.authenticated
    asyncio.run(run())


def test_bulk_records_segment_and_recover_loss(identities):
    async def run():
        _, members = identities
        a, b, queue = pair(members)
        delivered = []
        b.on_data = lambda data, path: delivered.append(data)
        await pump(a, b, queue, lambda: a.ready.is_set() and b.ready.is_set())
        a.transport.loss = b.transport.loss = 7
        payloads = [i.to_bytes(8, 'big') + bytes(MAX_RELIABLE_PAYLOAD - 8) for i in range(12)]
        for payload in payloads:
            assert a.send(payload)
        await pump(a, b, queue, lambda: len(delivered) == len(payloads) and not a.pending_count, timeout=12)
        assert delivered == payloads
        assert a.pending_bytes == 0
        assert max(a.transport.maximum_packet, b.transport.maximum_packet) <= 1260
    asyncio.run(run())
