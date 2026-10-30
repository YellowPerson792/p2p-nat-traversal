"""Maintenance tests distinguish control backpressure from real QUIC recovery."""
import asyncio
import time

from overlay.client import Client
from overlay.wire import DIRECT_MAGIC
from test_client import build
from test_identity import identities
from test_secure import pair, pump


def test_blocked_control_heartbeat_does_not_block_loss_recovery(identities):
    async def run():
        a, b, queue = pair(identities[1])
        started, release = asyncio.Event(), asyncio.Event()
        class WebSocket:
            async def send(self, message):
                started.set()
                await release.wait()
            async def close(self):
                pass
        a.ws = WebSocket()
        a.control_wakeup.set()  # Start the heartbeat now, without a five-second test delay.
        async def network():
            while True:
                while queue:
                    owner, data, source = queue.popleft()
                    owner.datagram_received(data, source)
                await asyncio.sleep(.001)
        received = []
        a.on_data = lambda data, path: received.append(data)
        b.on_data = lambda data, path: b.send(data)
        heartbeat = asyncio.create_task(a.control_maintenance(a.ws))
        tasks = [heartbeat, asyncio.create_task(a.tick()), asyncio.create_task(b.tick()),
                 asyncio.create_task(network())]
        try:
            await asyncio.wait_for(started.wait(), 1)
            await asyncio.wait_for(asyncio.gather(a.ready.wait(), b.ready.wait()), 2)
            a.transport.loss = b.transport.loss = 3
            payload = b'reliable while control is blocked' * 100
            assert a.send(payload)
            async with asyncio.timeout(2):
                while not received:
                    await asyncio.sleep(.001)
            assert received == [payload]
            assert not heartbeat.done() and not release.is_set()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(run())


def test_idle_data_scheduler_sleeps_until_a_real_event():
    async def run():
        client = Client(dict(server='127.0.0.1'), 'a', 'b')
        original = client.maintain
        iterations = 0
        ticked = asyncio.Event()
        def maintain(now):
            nonlocal iterations
            iterations += 1
            original(now)
            ticked.set()
        client.maintain = maintain
        task = asyncio.create_task(client.tick())
        try:
            await asyncio.wait_for(ticked.wait(), 1)
            await asyncio.sleep(.06)
            assert iterations == 1  # Old 10 ms polling would already have run several times.
            ticked.clear()
            client.wake()
            await asyncio.wait_for(ticked.wait(), .5)
            assert iterations == 2
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())


def test_idle_quic_survives_its_native_timeout_and_resumes_without_empty_hints(identities):
    async def run():
        a, b, queue = pair(identities[1])
        async def network():
            while True:
                while queue:
                    owner, data, source = queue.popleft()
                    owner.datagram_received(data, source)
                await asyncio.sleep(.001)
        tasks = [asyncio.create_task(a.tick()), asyncio.create_task(b.tick()),
                 asyncio.create_task(network())]
        try:
            await asyncio.wait_for(asyncio.gather(a.ready.wait(), b.ready.wait()), 2)
            connections = a.link.quic, b.link.quic
            original = a.transport.sendto
            empty_hints = []
            def send(data, destination):
                from overlay.wire import read_direct
                if not read_direct(data)[1]:
                    empty_hints.append(data)
                original(data, destination)
            a.transport.sendto = send
            await asyncio.sleep(11)  # Longer than configured QUIC idle_timeout=10.
            assert a.ready.is_set() and b.ready.is_set()
            assert not empty_hints
            received = []
            b.on_data = lambda data, path: received.append(data)
            assert a.send(b'after long idle')
            async with asyncio.timeout(2):
                while not received:
                    await asyncio.sleep(.001)
            assert received == [b'after long idle']
            assert (a.link.quic, b.link.quic) == connections
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(run())


def test_empty_nat_hints_back_off_and_stop_after_peer_authentication():
    client, _ = build()
    client.key = None
    client.peer_binding = object()
    class Link:
        authenticated = False
        available = True
        closed = False
        path = 'direct'
        def tick(self, now):
            pass
        def path_healthy(self, path, now):
            return path == 'direct'
    client.link = Link()
    intervals = []
    now = 100
    for _ in range(13):
        client.maintain(now)
        intervals.append(client.next_hole - now)
        now = client.next_hole
    assert intervals[0] == .25 and intervals[-1] == 8
    before = sum(packet.startswith(DIRECT_MAGIC) for packet, _ in client.transport.sent)
    client.link.authenticated = True
    client.maintain(now + 30)
    assert sum(packet.startswith(DIRECT_MAGIC) for packet, _ in client.transport.sent) == before


def test_registration_slows_after_authenticated_server_replies():
    client, _ = build()
    client.peer_binding = None
    client.session_ttl = 20
    client.maintain(100)
    assert client.next_register == 101
    client.last_registration_reply = 101
    client.maintain(101)
    assert client.next_register == 106
    client.maintain(120)  # No recent replies: accelerate registration recovery.
    assert client.next_register == 121


def test_idle_probe_budget_and_application_resume(identities, monkeypatch):
    async def run():
        a, b, queue = pair(identities[1])
        await pump(a, b, queue, lambda: a.ready.is_set() and b.ready.is_set())
        a.mode = 'auto'
        link, base = a.link, time.perf_counter()
        link.last_activity = link.active_since = base - 10
        link.last_probe_sent = dict(direct=0, relay=0)
        link.next_probe = dict(direct=0, relay=0)
        link.path_probes.clear()
        monkeypatch.setattr(link.quic, 'get_timer', lambda: None)
        monkeypatch.setattr(link, 'flush', lambda: None)
        monkeypatch.setattr(link, 'pending_count', lambda: 0)
        def no_redundant_ping(*args):
            raise AssertionError('Path nonce probes already provide liveness')
        monkeypatch.setattr(link.quic, 'send_ping', no_redundant_ping)
        probes, clock = [], [base]
        def route(path, data):
            probes.append((clock[0], path))
            link.path_probes.pop(path)  # Simulate a timely same-carrier reply.
            link.last_ack = link.path_seen[path] = clock[0]
        monkeypatch.setattr(link, 'route_datagram', route)
        for second in range(31):
            clock[0] = base + second
            link.tick(clock[0])
        assert sum(path == 'direct' for _, path in probes) <= 11
        assert sum(path == 'relay' for _, path in probes) <= 7
        assert len(probes) <= 18  # Previously 62 carrier probes plus 31 QUIC PINGs.
        clock[0] += .1
        link.note_activity(clock[0])
        assert link.probe_deadline('direct', clock[0]) <= clock[0]
        assert link.path_healthy('direct', clock[0])
    asyncio.run(run())


def test_backup_direct_probe_failures_have_a_bounded_backoff(identities, monkeypatch):
    async def run():
        a, b, queue = pair(identities[1])
        await pump(a, b, queue, lambda: a.ready.is_set() and b.ready.is_set())
        link, base = a.link, time.perf_counter()
        a.mode, link.path = 'auto', 'relay'
        link.last_activity = link.active_since = base - 10
        link.last_probe_sent = dict(direct=0, relay=0)
        link.next_probe = dict(direct=0, relay=0)
        link.path_probes.clear()
        monkeypatch.setattr(link.quic, 'get_timer', lambda: None)
        monkeypatch.setattr(link, 'flush', lambda: None)
        monkeypatch.setattr(link, 'pending_count', lambda: 0)
        attempts, clock = [], [base]
        def route(path, data):
            if path == 'direct':
                attempts.append(clock[0])  # No direct replies.
            else:
                link.path_probes.pop(path)
                link.last_ack = link.path_seen[path] = clock[0]
        monkeypatch.setattr(link, 'route_datagram', route)
        for second in range(31):
            clock[0] = base + second
            link.tick(clock[0])
        assert 2 < len(attempts) < 16
        assert 0 < link.probe_failures['direct']
        assert link.probe_interval('direct', clock[0]) == 4
        assert link.probe_deadline('direct', clock[0]) - clock[0] <= 4
    asyncio.run(run())
