import asyncio
import json
import pytest

from overlay.proxy import Proxy, UDP_LIMIT, WINDOW, DATA, frame, DatagramSocket
from test_secure import pair
from test_identity import identities, certificate, server as coordinator_server


async def network(a, b, queue):
    while True:
        while queue:
            owner, data, source = queue.popleft()
            owner.datagram_received(data, source)
        for client in (a, b):
            if client.link:
                client.link.tick(__import__('time').perf_counter())
            client.select_path()
        await asyncio.sleep(.001)


async def setup(members, target_port=5000, protocol='tcp'):
    a, b, queue = pair(members)
    left = Proxy({'target_port': target_port, 'protocol': protocol, 'initiator': True}, 'a', 'b', client=a)
    right = Proxy({'target_port': target_port, 'protocol': protocol, 'initiator': False}, 'b', 'a', client=b)
    pump = asyncio.create_task(network(a, b, queue))
    await asyncio.wait_for(asyncio.gather(a.ready.wait(), b.ready.wait()), 5)
    return left, right, pump


async def cleanup(left, right, pump):
    for proxy in (left, right):
        for key in list(proxy.forwards):
            await proxy.remove_forward(key)
        proxy.reset()
        tasks = list(proxy.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    pump.cancel()
    await asyncio.gather(pump, return_exceptions=True)


def test_tcp_large_transfer_half_close_and_concurrent_channels(identities):
    async def run():
        async def service(reader, writer):
            data = await reader.read()
            writer.write(data[::-1])
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        server = await asyncio.start_server(service, '127.0.0.1', 0)
        left, right, pump = await setup(identities[1], server.sockets[0].getsockname()[1])
        try:
            mapping = await left.add_forward('tcp')
            left.client.transport.loss = right.client.transport.loss = 11
            async def transfer(payload):
                reader, writer = await asyncio.open_connection(*mapping['address'])
                writer.write(payload)
                await writer.drain()
                writer.write_eof()
                assert await reader.read() == payload[::-1]
                writer.close()
                await writer.wait_closed()
            await asyncio.wait_for(asyncio.gather(
                transfer(bytes(range(256)) * 4800), transfer(b'another channel' * 3000)), 25)
            async with asyncio.timeout(5):
                while (left.client.streams or right.client.streams
                       or left.client.pending_bytes or right.client.pending_bytes):
                    await asyncio.sleep(.01)
            assert not left.channels and not right.channels
            assert not left.client.pending_bytes and not right.client.pending_bytes
        finally:
            await cleanup(left, right, pump)
            server.close()
            await server.wait_closed()
    asyncio.run(run())


def test_udp_preserves_datagrams_and_separates_local_senders(identities):
    async def run():
        loop = asyncio.get_running_loop()
        echo = await DatagramSocket.create(asyncio.create_task,
            lambda data, addr: echo.sendto(data, addr))
        left, right, pump = await setup(identities[1], echo.get_extra_info('sockname')[1], 'udp')
        sockets = []
        try:
            mapping = await left.add_forward('udp')
            import socket
            for payload in (b'', b'first', bytes(UDP_LIMIT)):
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setblocking(False)
                sockets.append(sock)
                await loop.sock_sendto(sock, payload, tuple(mapping['address']))
                result, _ = await asyncio.wait_for(loop.sock_recvfrom(sock, 2048), 5)
                assert result == payload
            assert len(left.channels) == len(right.channels) == 3
            await loop.sock_sendto(sockets[0], b'reused sender', tuple(mapping['address']))
            result, _ = await asyncio.wait_for(loop.sock_recvfrom(sockets[0], 2048), 5)
            assert result == b'reused sender' and len(left.udp_channels) == 3
            old = left.udp_channels[(mapping['id'], ('127.0.0.1', sockets[0].getsockname()[1]))]
            left.drop(old, notify=True)
            assert len(left.udp_channels) == 2
            await loop.sock_sendto(sockets[0], b'reopened sender', tuple(mapping['address']))
            result, _ = await asyncio.wait_for(loop.sock_recvfrom(sockets[0], 2048), 5)
            assert result == b'reopened sender' and len(left.udp_channels) == 3
            await loop.sock_sendto(sockets[0], bytes(UDP_LIMIT + 1), tuple(mapping['address']))
            await asyncio.sleep(.03)
            assert left.dropped_datagrams == 1
            await left.remove_forward(mapping['id'])
            await asyncio.sleep(.1)
            assert not left.channels and not right.channels
            assert not left.udp_channels
        finally:
            for sock in sockets:
                sock.close()
            await cleanup(left, right, pump)
            echo.close()
    asyncio.run(run())


def test_tcp_batching_preserves_backpressure_bytes_and_half_close(identities):
    async def run():
        from overlay.proxy import CHUNK, CREDIT, IO_BATCH
        left, right, pump = await setup(identities[1])
        release = asyncio.Event()
        entered = asyncio.Event()
        credits = []

        class Writer:
            def __init__(self):
                self.data = bytearray()
                self.drains = 0
                self.eof = False

            def write(self, data):
                assert not self.eof
                self.data.extend(data)

            async def drain(self):
                self.drains += 1
                entered.set()
                await release.wait()

            def write_eof(self):
                self.eof = True

            def close(self):
                pass

        async def send(c, kind, body=b''):
            assert kind == CREDIT
            credits.append(int.from_bytes(body, 'big'))

        try:
            c = left.new_channel('tcp', True)
            c.writer = Writer()
            payload = bytes(range(256)) * (CHUNK // 256)
            count = 24
            c.allowance = WINDOW - len(payload) * count
            for _ in range(count):
                c.inbox.put_nowait(payload)
            c.inbox.put_nowait(None)
            left.send = send
            task = left.spawn(left.write_tcp(c), c)
            await asyncio.wait_for(entered.wait(), 1)
            assert not credits and c.allowance == WINDOW - len(payload) * count
            assert len(c.writer.data) <= IO_BATCH + CHUNK
            release.set()
            await asyncio.wait_for(task, 2)
            assert c.writer.data == payload * count
            assert sum(credits) == len(payload) * count and c.allowance == WINDOW
            assert len(credits) < count and c.writer.drains < count
            assert c.writer.eof and c.drained_eof
        finally:
            release.set()
            await cleanup(left, right, pump)
    asyncio.run(run())


def test_peer_cannot_choose_arbitrary_target_and_reset_closes_channels(identities):
    async def run():
        left, right, pump = await setup(identities[1])
        try:
            from overlay.proxy import OPEN
            right.receive(frame(OPEN, bytes(16), json.dumps({
                'protocol': 'tcp', 'host': '192.0.2.1', 'port': 22}).encode()), 'direct')
            assert not right.channels
            c = left.new_channel('tcp', True)
            left.client.reset_peer()
            assert c.closed and not left.channels
        finally:
            await cleanup(left, right, pump)
    asyncio.run(run())


def test_receive_credit_bounds_slow_consumer(identities):
    async def run():
        left, right, pump = await setup(identities[1])
        try:
            c = left.new_channel('tcp', True)
            c.ready.set()
            c.allowance = 1
            left.receive(frame(DATA, c.key, b'too much'), 'direct')
            assert c.closed
            assert len(left.channels) == 0
        finally:
            await cleanup(left, right, pump)
    asyncio.run(run())


def test_quic_stream_cannot_address_another_proxy_channel(identities):
    async def run():
        left, right, pump = await setup(identities[1])
        try:
            from overlay.proxy import READY
            first = left.new_channel('tcp', True)
            second = left.new_channel('tcp', True)
            await right.client.send_reliable(frame(READY, second.key), stream=first.key)
            async with asyncio.timeout(5):
                while not first.closed:
                    await asyncio.sleep(.01)
            assert second.key in left.channels and not second.ready.is_set()
        finally:
            await cleanup(left, right, pump)
    asyncio.run(run())


def test_approved_scope_rejects_other_ports_protocols_and_reverse_open(identities):
    async def run():
        from overlay.proxy import OPEN
        left, right, pump = await setup(identities[1], 5000, 'tcp')
        try:
            for proxy, scope in (
                (right, {'protocol': 'tcp', 'port': 5001}),
                (right, {'protocol': 'udp', 'port': 5000}),
                (right, {'protocol': 'tcp', 'port': True}),
                (left, {'protocol': 'tcp', 'port': 5000}),
            ):
                proxy.receive(frame(OPEN, bytes(16), json.dumps(scope).encode()), 'direct')
                assert not proxy.channels
            with pytest.raises(ValueError):
                await right.add_forward('tcp')
            with pytest.raises(ValueError):
                await left.add_forward('udp')
        finally:
            await cleanup(left, right, pump)
    asyncio.run(run())



def test_live_proxy_tcp_survives_direct_relay_switch(identities, certificate):
    async def run():
        async def echo(reader, writer):
            try:
                while data := await reader.read(8192):
                    writer.write(data)
                    await writer.drain()
                writer.write_eof()
            finally:
                writer.close()
                await writer.wait_closed()
        listener = await asyncio.start_server(echo, '127.0.0.1', 0)
        registry, members = identities
        async with coordinator_server(registry, certificate, target_port=listener.sockets[0].getsockname()[1]) as (config, coordinator):
            left = Proxy(dict(config, identity=members['a']['identity'],
                              peer_fingerprint=members['b']['fingerprint'], target_port=listener.sockets[0].getsockname()[1], protocol='tcp', initiator=True), 'a', 'b')
            right = Proxy(dict(config, identity=members['b']['identity'],
                               peer_fingerprint=members['a']['fingerprint'],
                               target_port=listener.sockets[0].getsockname()[1], protocol='tcp', initiator=False), 'b', 'a')
            try:
                await left.client.start()
                await right.client.start()
                async with asyncio.timeout(10):
                    while not all(p.client.ready.is_set() and p.client.path == 'direct'
                                  for p in (left, right)):
                        await asyncio.sleep(.02)
                mapping = await left.add_forward('tcp')
                reader, writer = await asyncio.open_connection(*mapping['address'])
                reader2, writer2 = await asyncio.open_connection(*mapping['address'])
                for mode in ('direct-only', 'relay-only', 'auto'):
                    for p in (left, right):
                        p.client.mode = mode
                        p.client.select_path()
                    data = (mode.encode() + b' payload') * 3000
                    writer.write(data)
                    writer2.write(b'second stream:' + data)
                    await asyncio.gather(writer.drain(), writer2.drain())
                    replies = await asyncio.wait_for(asyncio.gather(
                        reader.readexactly(len(data)), reader2.readexactly(len(data) + 14)), 10)
                    assert replies == [data, b'second stream:' + data]
                    assert len(left.client.link.send_streams) == 2
                assert left.client.metrics['relay_received'] > 0
                assert right.client.metrics['relay_received'] > 0
                writer.write_eof()
                assert await asyncio.wait_for(reader.read(), 5) == b''
                writer.close()
                await writer.wait_closed()
                writer2.write(b'other stream stays open')
                await writer2.drain()
                assert await asyncio.wait_for(reader2.readexactly(23), 5) == b'other stream stays open'
                writer2.write_eof()
                assert await asyncio.wait_for(reader2.read(), 5) == b''
                writer2.close()
                await writer2.wait_closed()
            finally:
                await left.close()
                await right.close()
        listener.close()
        await listener.wait_closed()
    from overlay.runtime import run as run_async
    run_async(run())
