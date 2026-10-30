import asyncio
from contextlib import asynccontextmanager
import json
import socket
import ssl
import stat

import pytest
import uvicorn
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from overlay.client import Client
from overlay.identity import (Policy, approve, initialize,
                              openssl, proof, revoke, sign, verify, write_json, fingerprint)
from overlay.server import create_app
from overlay.wire import pack, read_direct, read_relay, relay_packet


class RecordingTransport:
    def __init__(self, transport):
        self.transport, self.sent = transport, []

    def sendto(self, data, address):
        self.sent.append((data, address))
        self.transport.sendto(data, address)

    def __getattr__(self, name):
        return getattr(self.transport, name)


@pytest.fixture
def identities(tmp_path):
    result = {}
    registry = tmp_path / "members.json"
    for node in ('a', 'b', 'observer'):
        key, request = tmp_path / f"{node}.key", tmp_path / f"{node}.request.json"
        identity = initialize(node, key, request)
        approve(registry, request, identity["fingerprint"], metrics=node == "observer")
        result[node] = {"node": node, "identity": str(key), "request": request, **identity}
    return registry, result


def test_retired_peer_lists_are_not_permissions_and_are_removed_on_write(identities):
    registry, members = identities
    data = json.loads(registry.read_text())
    data['members']['a']['peers'] = ['b']
    write_json(registry, data)
    assert 'peers' not in Policy(registry).grant('a')
    approve(registry, members['a']['request'], members['a']['fingerprint'])
    assert all('peers' not in value for value in json.loads(registry.read_text())['members'].values())
    with pytest.raises(ValueError, match='boolean'):
        approve(registry, members['a']['request'], members['a']['fingerprint'], ['b'])


def test_independent_keys_wrong_proof_and_context_binding(identities):
    registry, members = identities
    challenge = {"kind": "challenge", "version": 1, "node": "a", "peer": "b",
                 "purpose": "connect", "fingerprint": members["a"]["fingerprint"], "nonce": "01" * 32}
    response = proof(members["a"]["identity"], challenge, "a", "b", "connect")
    verify(members["a"]["public_key"], challenge, response["signature"])
    with pytest.raises(ValueError):
        verify(members["b"]["public_key"], challenge, response["signature"])
    for field, changed in (("nonce", "02" * 32), ("peer", "c"), ("purpose", "metrics"), ("node", "b")):
        with pytest.raises(ValueError):
            verify(members["a"]["public_key"], {**challenge, field: changed}, response["signature"])
    with pytest.raises(ValueError):
        proof(members["a"]["identity"], {**challenge, "purpose": "metrics"}, "a", "b", "connect")


def test_approval_is_explicit_and_fails_closed(identities, tmp_path):
    registry, members = identities
    policy = Policy(registry)
    assert policy.grant("a", expected=members["a"]["fingerprint"])["enabled"]
    assert "peers" not in policy.grant("a")
    with pytest.raises(ValueError):
        policy.grant("b", expected=members["a"]["fingerprint"])
    with pytest.raises(ValueError):
        policy.grant("a", purpose="metrics")
    with pytest.raises(ValueError):
        approve(registry, members["a"]["request"], "00" * 32)
    attacker = initialize("a", tmp_path / "other.key", tmp_path / "other.request.json")
    with pytest.raises(ValueError):
        approve(registry, tmp_path / "other.request.json", attacker["fingerprint"])
    # 同一公钥换个十六进制字母大小写，也不能绕过“一节点一身份”的绑定。
    alias = {"version": 1, "node": "alias", "public_key": members["a"]["public_key"].upper(),
             "fingerprint": members["a"]["fingerprint"]}
    write_json(tmp_path / "alias.request.json", alias)
    with pytest.raises(ValueError):
        approve(registry, tmp_path / "alias.request.json", members["a"]["fingerprint"])
    registry.chmod(0o640)
    approve(registry, members["a"]["request"], members["a"]["fingerprint"])
    import os
    if os.name != "nt":
        assert stat.S_IMODE(registry.stat().st_mode) == 0o640
    revoke(registry, "a")
    with pytest.raises(ValueError):
        policy.grant("a", expected=members["a"]["fingerprint"])
    registry.write_text("broken registry")
    with pytest.raises(ValueError):
        policy.grant("b", expected=members["b"]["fingerprint"])
    registry.unlink()
    assert policy.load() == {}


def test_data_plane_policy_cache_is_bounded_and_force_refresh_bypasses_it(identities, monkeypatch):
    registry, members = identities
    clock = [100.0]
    monkeypatch.setattr("overlay.identity.time.monotonic", lambda: clock[0])
    policy = Policy(registry, reload_interval=0.1)
    original = policy.path
    class CountedPath:
        calls = 0
        def stat(self):
            self.calls += 1
            return original.stat()
        def read_text(self, **kwargs):
            return original.read_text(**kwargs)
    policy.path = CountedPath()
    for _ in range(30):
        assert policy.grant("a", expected=members["a"]["fingerprint"])["enabled"]
    assert policy.path.calls == 1
    revoke(registry, "a")
    clock[0] += 0.101
    with pytest.raises(ValueError):
        policy.grant("a", expected=members["a"]["fingerprint"])
    revoke(registry, "b")
    policy.load(force=True)
    with pytest.raises(ValueError):
        policy.grant("b", expected=members["b"]["fingerprint"])


@pytest.fixture(scope="module")
def certificate(tmp_path_factory):
    folder = tmp_path_factory.mktemp("identity-tls")
    cert, key = folder / "server.crt", folder / "server.key"
    openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
            "-keyout", key, "-out", cert, "-subj", "/CN=overlay-test",
            "-addext", "subjectAltName=IP:127.0.0.1")
    return cert, key


@asynccontextmanager
async def server(registry, certificate, target_port=5000):
    cert, key = certificate
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    app = create_app(registry, "127.0.0.1", 0)
    # Construct ordinary valid consent records; no production authorization bypass.
    from overlay.directory import Presence, Consent
    class DirectorySocket:
        async def send_json(self, message):
            pass
        async def close(self, **kwargs):
            pass
    directory = app.state.coordinator.directory
    for node in ('a', 'b'):
        member = directory.member(node)
        directory.online[node] = Presence(node, fingerprint(member['public_key']), DirectorySocket())
    directory.consents['test-consent'] = Consent(directory.online['a'], directory.online['b'], target_port, 'tcp')
    runtime = uvicorn.Server(uvicorn.Config(app, ssl_certfile=str(cert), ssl_keyfile=str(key),
                                          log_level="critical", ws_max_size=16384))
    task = asyncio.create_task(runtime.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not runtime.started:
                if task.done():
                    await task
                    raise RuntimeError("Test server did not start")
                await asyncio.sleep(0.01)
        config = {"server": "127.0.0.1", "control_port": listener.getsockname()[1], "ca": str(cert),
                  "udp_port": app.state.coordinator.port, "consent_ticket": "test-consent"}
        yield config, app.state.coordinator
    finally:
        runtime.should_exit = True
        await asyncio.wait_for(task, 5)
        listener.close()


def ws_options(config):
    return {"ssl": ssl.create_default_context(cafile=config["ca"]), "proxy": None}


def url(config):
    return f'wss://127.0.0.1:{config["control_port"]}/ws'


def test_real_tls_rejects_unapproved_impersonation_and_replayed_proof(identities, certificate):
    async def run():
        registry, members = identities
        async with server(registry, certificate) as (config, coordinator):
            # 旧 token 和未知节点都没有加入权限。
            async with connect(url(config), additional_headers={"Authorization": "Bearer old-token"},
                               **ws_options(config)) as ws:
                await ws.send(json.dumps({"node": "unknown", "peer": "b"}))
                with pytest.raises(ConnectionClosed):
                    await ws.recv()
            assert not coordinator.sessions
            async with connect(url(config), **ws_options(config)) as ws:
                await ws.send(json.dumps({"node": "a", "peer": "observer"}))
                with pytest.raises(ConnectionClosed):
                    await ws.recv()
            async with connect(url(config), **ws_options(config)) as ws:
                await ws.send(json.dumps({"node": "a", "peer": "b", "ticket": config["consent_ticket"]}))
                challenge = json.loads(await ws.recv())
                await ws.send(json.dumps({"kind": "proof", "signature": sign(members["b"]["identity"], challenge)}))
                with pytest.raises(ConnectionClosed):
                    await ws.recv()
            # 一次连接的正确签名不能放到另一次连接上。
            async with connect(url(config), **ws_options(config)) as ws:
                await ws.send(json.dumps({"node": "a", "peer": "b", "ticket": config["consent_ticket"]}))
                challenge = json.loads(await ws.recv())
                response = proof(members["a"]["identity"], challenge, "a", "b", "connect")
                await ws.send(json.dumps(response))
                assert json.loads(await ws.recv())["kind"] == "welcome"
            async with connect(url(config), **ws_options(config)) as ws:
                await ws.send(json.dumps({"node": "a", "peer": "b", "ticket": config["consent_ticket"]}))
                next_challenge = json.loads(await ws.recv())
                assert next_challenge["nonce"] != challenge["nonce"]
                await ws.send(json.dumps(response))
                with pytest.raises(ConnectionClosed):
                    await ws.recv()
            # Keep this in-process TLS permission test fully asynchronous.
            async with connect(url(config), **ws_options(config)) as ws:
                await ws.send(json.dumps({'node': 'observer', 'peer': None, 'purpose': 'metrics'}))
                challenge = json.loads(await ws.recv())
                response = await asyncio.to_thread(proof, members['observer']['identity'],
                                                    challenge, 'observer', None, 'metrics')
                await ws.send(json.dumps(response))
                metrics = json.loads(await ws.recv())['metrics']
                assert 'online_nodes' in metrics
            async with connect(url(config), **ws_options(config)) as ws:
                await ws.send(json.dumps({'node': 'a', 'peer': None, 'purpose': 'metrics'}))
                with pytest.raises(ConnectionClosed):
                    await ws.recv()
    from overlay.runtime import run as run_async
    run_async(run())


@pytest.mark.parametrize("mode", ["auto", "relay-only", "direct-only"])
def test_authenticated_quic_clients_exchange_and_revocation(identities, certificate, mode):
    async def run():
        registry, members = identities
        async with server(registry, certificate) as (config, coordinator):
            received = []
            a = Client({**config, **members["a"]}, "a", "b", mode,
                       on_data=lambda data, path: received.append((data, path)))
            b = Client({**config, **members["b"]}, "b", "a", mode,
                       on_data=lambda data, path: b.send(data))
            await a.start()
            await b.start()
            a.transport = RecordingTransport(a.transport)
            b.transport = RecordingTransport(b.transport)
            try:
                async with asyncio.timeout(8):
                    while not (a.ready.is_set() and b.ready.is_set() and
                               (mode != "auto" or a.path == b.path == "direct")):
                        await asyncio.sleep(0.01)
                assert all(not hasattr(allocation, "key") for allocation in coordinator.allocations.values())
                assert a.send(b"approved hello")
                async with asyncio.timeout(3):
                    while not received or a.pending_count:
                        await asyncio.sleep(0.01)
                assert received == [(b"approved hello", "relay" if mode == "relay-only" else "direct")]
                if mode == "direct-only":
                    assert coordinator.counters["relay_packets"] == 0
                assert a.send(bytes(900))
                async with asyncio.timeout(3):
                    while len(received) != 2:
                        await asyncio.sleep(0.01)
                assert received[-1][0] == bytes(900)
                # 实际 UDP 不含应用明文；所有 QUIC 数据报都受 1200 字节上限约束。
                binary = [(data, address) for data, address in a.transport.sent if data.startswith((b"OD3", b"OR3"))]
                assert binary
                for data, address in binary:
                    raw = read_relay(data, a.key)[-1] if address == a.server else read_direct(data)[-1]
                    assert len(raw) <= 1200
                    assert b"approved hello" not in raw
                    assert len(data) <= 1260
                count = len(received)
                data, address = next((d, addr) for d, addr in reversed(binary) if len(d) > 100)
                if address == a.server:
                    allocation_id, side, _, packet = read_relay(data, a.key)
                    damaged = packet[:-1] + bytes([packet[-1] ^ 1])
                    b.datagram_received(relay_packet(allocation_id, side, b.key, 100000, damaged), b.server)
                else:
                    damaged = data[:-1] + bytes([data[-1] ^ 1])
                    b.datagram_received(damaged, b.peer_endpoint)
                await asyncio.sleep(0.05)
                assert len(received) == count
                session = coordinator.nodes[("test-consent", "a")]
                revoke(registry, "a")
                await asyncio.sleep(0.15)  # 数据面审批缓存最多 100ms。
                before = coordinator.counters["rejected_packets"]
                coordinator.datagram_received(pack({"kind": "register", "sid": session.sid,
                    "seq": a.outer_seq + 1}, session.key), tuple(a.metrics["local_endpoint"]))
                assert coordinator.counters["rejected_packets"] == before + 1
                async with asyncio.timeout(3):
                    while coordinator.allocations or b.link or ("test-consent", "a") in coordinator.nodes or a.ready.is_set():
                        await asyncio.sleep(0.02)
                assert not b.send(b"after revocation")
            finally:
                await asyncio.gather(a.close(), b.close())
    asyncio.run(run())


def test_shared_quic_failover_preserves_order_and_native_connection(identities, certificate):
    async def run():
        registry, members = identities
        async with server(registry, certificate) as (config, coordinator):
            received = []
            a = Client({**config, **members["a"], "peer_fingerprint": members["b"]["fingerprint"]},
                       "a", "b", on_data=lambda data, path: received.append((data, path)))
            b = Client({**config, **members["b"]}, "b", "a", on_data=lambda data, path: b.send(data))
            await a.start()
            await b.start()
            async def until(predicate, timeout=8):
                async with asyncio.timeout(timeout):
                    while not predicate():
                        await asyncio.sleep(0.02)
            try:
                await until(lambda: a.path == b.path == "direct" and
                            a.link and a.link.available and
                            b.link and b.link.available)
                backup, allocation_id = a.link, a.allocation_id
                connection, original_cid = a.link.quic, a.link.original_cid
                a.drop_peer_udp = b.drop_peer_udp = True
                for number in range(30):
                    assert a.send(number.to_bytes(8, "big"))
                    await asyncio.sleep(0.01)
                await until(lambda: len(received) == 30)
                assert [int.from_bytes(data, "big") for data, _ in received] == list(range(30))
                assert a.link is backup and a.allocation_id == allocation_id
                assert a.path == b.path == "relay"
                assert a.link.quic is connection and a.link.original_cid == original_cid
                a.drop_peer_udp = b.drop_peer_udp = False
                await until(lambda: a.path == b.path == "direct", 12)
                # QUIC 原地更新密钥，无需销毁配对或重新登录。
                a.link.updated_key -= 541
                assert a.send(b"new keys")
                await until(lambda: len(received) == 31)
                assert received[-1] == (b"new keys", "direct")
                assert a.allocation_id == allocation_id
                # 地址变化只让直连重新探测，保留同一 QUIC 和流偏移。
                other = coordinator.nodes[("test-consent", "b")]
                endpoint = other.endpoint
                other.endpoint = (endpoint[0], endpoint[1] % 60000 + 1)
                await coordinator.publish(other)
                await until(lambda: a.peer_endpoint == other.endpoint)
                assert a.link is backup and a.allocation_id == allocation_id
                other.endpoint = endpoint
                await coordinator.publish(other)
                a.credentials.created -= 23 * 3600
                await until(lambda: a.allocation_id is not None and a.allocation_id != allocation_id and a.ready.is_set() and b.ready.is_set(), 12)
                assert a.send(b"renewed certificate")
                await until(lambda: len(received) == 32)
                assert received[-1][0] == b"renewed certificate"
            finally:
                await asyncio.gather(a.close(), b.close())
    asyncio.run(run())


def test_wrong_peer_pin_prevents_all_application_connections(identities, certificate):
    async def run():
        registry, members = identities
        async with server(registry, certificate) as (config, coordinator):
            a = Client({**config, **members["a"], "peer_fingerprint": "00" * 32}, "a", "b")
            b = Client({**config, **members["b"]}, "b", "a")
            await a.start()
            await b.start()
            try:
                await asyncio.sleep(2)
                assert not a.ready.is_set() and not a.link
                assert not a.send(b"must fail")
            finally:
                await asyncio.gather(a.close(), b.close())
    asyncio.run(run())
