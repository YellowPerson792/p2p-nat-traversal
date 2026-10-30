import asyncio
import time
from dataclasses import replace

import pytest

from overlay.server import Coordinator, Session
from overlay.wire import pack, read_relay, relay_packet
from overlay.identity import write_json, fingerprint


class Transport:
    def __init__(self):
        self.sent = []
    def sendto(self, packet, endpoint):
        self.sent.append((packet, endpoint))


class Socket:
    async def send_json(self, value):
        pass
    async def close(self, **kwargs):
        pass


def build(tmp_path):
    public_a = "302a300506032b6570032100" + "ab" * 32
    public_b = "302a300506032b6570032100" + "cd" * 32
    registry = tmp_path / "members.json"
    write_json(registry, {"version": 1, "members": {
        "a": {"public_key": public_a, "enabled": True},
        "b": {"public_key": public_b, "enabled": True}}})
    server = Coordinator(registry, "127.0.0.1", 3478, ttl=1)
    server.transport = Transport()
    a = Session("a", "b", "aa" * 16, "ab" * 32, Socket(), endpoint=("127.0.0.1", 1001), fingerprint=fingerprint(public_a))
    b = Session("b", "a", "bb" * 16, "cd" * 32, Socket(), endpoint=("127.0.0.1", 1002), fingerprint=fingerprint(public_b))
    from overlay.directory import Presence, Consent
    directory = server.directory
    directory.online = {'a': Presence('a', a.fingerprint, Socket()), 'b': Presence('b', b.fingerprint, Socket())}
    directory.consents['test-consent'] = Consent(directory.online['a'], directory.online['b'], 5000, 'tcp')
    a.consent = b.consent = 'test-consent'
    server.nodes, server.sessions = {("test-consent", "a"): a, ("test-consent", "b"): b}, {a.sid: a, b.sid: b}
    server.create_allocation(a, b)
    return server, a, b


def test_relay_authenticates_and_forwards_opaque_quic_without_termination(tmp_path):
    server, a, b = build(tmp_path)
    payload = b"opaque" * 100
    packet = relay_packet(a.allocation_id, 0, a.key, 1, payload)
    server.datagram_received(packet, a.endpoint)
    assert server.counters["relay_packets"] == 1
    forwarded, address = server.transport.sent[-1]
    assert address == b.endpoint
    assert read_relay(forwarded, b.key)[-1] == payload
    server.datagram_received(packet, a.endpoint)
    server.datagram_received(relay_packet(a.allocation_id, 0, "ff" * 32, 2, payload), a.endpoint)
    server.datagram_received(relay_packet("00" * 16, 0, a.key, 3, payload), a.endpoint)
    assert server.counters["rejected_packets"] == 3


def test_relay_rejects_wrong_source_expiry_and_legacy_json(tmp_path):
    server, a, b = build(tmp_path)
    server.datagram_received(relay_packet(a.allocation_id, 0, a.key, 1, b"x"), ("127.0.0.1", 999))
    b.last_seen = time.monotonic() - 5
    server.datagram_received(relay_packet(a.allocation_id, 0, a.key, 2, b"x"), a.endpoint)
    server.datagram_received(pack({"kind": "relay", "sid": a.sid, "seq": 3, "packet": "legacy"}, a.key), a.endpoint)
    assert server.counters["rejected_packets"] == 3
    assert not server.transport.sent


def test_allocation_is_invalidated_when_peer_reconnects_without_replacing_sender(tmp_path):
    async def run():
        server, a, b = build(tmp_path)
        old_id, sender_sid = a.allocation_id, a.sid
        late = relay_packet(old_id, 0, a.key, 10, b'old allocation bytes')
        await server.remove(b, 'reconnect')
        assert old_id not in server.allocations and a.allocation_id is None
        replacement = replace(b, sid='cc' * 16, key='ef' * 32,
                              allocation_id=None, endpoint=('127.0.0.1', 1003),
                              last_seen=time.monotonic())
        server.sessions[replacement.sid] = replacement
        server.nodes[(replacement.consent, replacement.node)] = replacement
        allocation = server.create_allocation(a, replacement)
        assert a.sid == sender_sid and allocation.id != old_id
        server.datagram_received(late, a.endpoint)
        assert server.counters['rejected_packets'] == 1
        assert not server.transport.sent
        server.datagram_received(relay_packet(allocation.id, 0, a.key, 11, b'new bytes'), a.endpoint)
        assert server.transport.sent[-1][1] == replacement.endpoint
        assert read_relay(server.transport.sent[-1][0], replacement.key) == (allocation.id, 0, 1, b'new bytes')
    asyncio.run(run())


def test_allocation_direction_requires_its_own_session_key(tmp_path):
    server, a, b = build(tmp_path)
    allocation_id = a.allocation_id
    server.datagram_received(relay_packet(allocation_id, 1, a.key, 1, b'wrong side'), a.endpoint)
    assert server.counters['rejected_packets'] == 1 and not server.transport.sent
    server.datagram_received(relay_packet(allocation_id, 1, b.key, 1, b'reverse bytes'), b.endpoint)
    forwarded, endpoint = server.transport.sent[-1]
    assert endpoint == a.endpoint
    assert read_relay(forwarded, a.key) == (allocation_id, 1, 1, b'reverse bytes')


def test_endpoint_update_retains_the_same_allocation(tmp_path):
    async def run():
        server, a, b = build(tmp_path)
        allocation = server.allocations[a.allocation_id]
        server.datagram_received(pack(dict(kind='register', sid=b.sid, seq=1), b.key), ('127.0.0.1', 1102))
        await asyncio.gather(*server.tasks)
        assert server.allocations[a.allocation_id] is allocation
        assert b.allocation_id == allocation.id
        server.datagram_received(relay_packet(allocation.id, 0, a.key, 1, b'moved endpoint'), a.endpoint)
        assert server.transport.sent[-1][1] == ('127.0.0.1', 1102)
    asyncio.run(run())


def test_allocation_cannot_join_different_consent_scopes(tmp_path):
    server, a, b = build(tmp_path)
    original_id = a.allocation_id
    different = replace(b, consent='other-consent', sid='cc' * 16)
    server.sessions[different.sid] = different
    with pytest.raises(ValueError):
        server.create_allocation(a, different)
    assert original_id in server.allocations and a.allocation_id == original_id


def test_expired_session_is_removed_and_pair_invalidated(tmp_path):
    async def scenario():
        server, a, b = build(tmp_path)
        a.last_seen = b.last_seen = time.monotonic() - 5
        task = asyncio.create_task(server.reap())
        try:
            async with asyncio.timeout(4):
                while server.sessions:
                    await asyncio.sleep(0.02)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert not server.allocations and server.counters["expired_sessions"] == 2
    asyncio.run(scenario())
