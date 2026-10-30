"""Real TLS/QUIC connections sharing device identities but isolating port grants."""
import asyncio
import json
import socket
import secrets

import pytest

from overlay.control import create_app as control_app
from overlay.device import Device
from overlay.proxy import DatagramSocket
from overlay.server import create_app as server_app
from test_device import serve, wait_until, http, cli
from test_identity import certificate


def test_multiple_ports_peers_and_independent_lifecycles(tmp_path, certificate):
    async def scenario():
        listeners = []
        devices = [Device(tmp_path / name / 'client.json') for name in ('a', 'b', 'c')]
        a, b, c = devices
        app = server_app(tmp_path / 'members.json', '127.0.0.1', 0, admin_token='x' * 64)
        directory = app.state.coordinator.directory
        token = tmp_path / 'control-token'
        token.write_text('t' * 64)

        async def tcp_target(prefix):
            async def echo(reader, writer):
                try:
                    while data := await reader.read(4096):
                        writer.write(prefix + data)
                        await writer.drain()
                finally:
                    writer.close()
                    await writer.wait_closed()
            listener = await asyncio.start_server(echo, '127.0.0.1', 0)
            listeners.append(listener)
            return listener.sockets[0].getsockname()[1]

        p1, p2, p3 = await tcp_target(b'B1:'), await tcp_target(b'B2:'), await tcp_target(b'C:')
        udp = await DatagramSocket.create(asyncio.create_task,
            lambda data, addr: udp.sendto(b'UDP:' + data, addr))
        udp_port = udp.get_extra_info('sockname')[1]

        async def tcp_check(key, prefix):
            proxy = a.connections[key].proxy
            address = next(iter(proxy.forwards.values()))['address']
            reader, writer = await asyncio.open_connection(*address)
            try:
                writer.write(b'test')
                await writer.drain()
                assert await asyncio.wait_for(reader.readexactly(len(prefix) + 4), 5) == prefix + b'test'
            finally:
                writer.close()
                await writer.wait_closed()

        async def ready(key, recipient):
            await wait_until(lambda: key in a.connections and key in recipient.connections
                and a.connections[key].proxy.client.ready.is_set()
                and recipient.connections[key].proxy.client.ready.is_set())

        async def request(control_port, peer, port, protocol='tcp', local_port=0):
            response = await http(control_port, '/api/requests', 'POST',
                dict(peer=peer, port=port, protocol=protocol, local_port=local_port), token='t' * 64)
            assert response[0] == 202
            return json.loads(response[1])['id']

        try:
            async with serve(app, certificate) as server_port, serve(control_app(a, 't' * 64)) as control_port:
                for name, device in zip(('a', 'b', 'c'), devices):
                    await device.generate(name)
                    await device.setup(f'https://127.0.0.1:{server_port}', ca_path=str(certificate[0]))
                    await device.join()
                await wait_until(lambda: len(directory.pending) == 3)
                for key, pending in list(directory.pending.items()):
                    directory.decide_enrollment(key, True, pending['request']['fingerprint'])
                await wait_until(lambda: all(d.state == 'online' and len(d.peers) == 2 for d in devices))

                first, second, third, datagram = await asyncio.gather(
                    request(control_port, 'b', p1), request(control_port, 'b', p2),
                    request(control_port, 'c', p3), request(control_port, 'b', udp_port, 'udp'))
                await wait_until(lambda: len(b.requests) == 3 and len(c.requests) == 1)
                assert not a.connections and not b.connections
                await b.respond(first, True)
                await c.respond(third, True)
                await ready(first, b)
                await ready(third, c)
                # Two more pending ports do not block or authorize themselves.
                assert second in b.requests and datagram in b.requests
                assert second not in a.connections and datagram not in a.connections
                await asyncio.gather(tcp_check(first, b'B1:'), tcp_check(third, b'C:'))

                # A different device cannot close a connection it does not participate in.
                with pytest.raises(ValueError, match='Connection not found'):
                    await directory.command(directory.online['c'], dict(kind='disconnect', id=first))
                # Rejecting one pending request does not affect the other request or live ports.
                await b.respond(second, False)
                await wait_until(lambda: second not in a.requests)
                await tcp_check(first, b'B1:')
                second = await request(control_port, 'b', p2)
                await wait_until(lambda: second in b.requests)
                await b.respond(second, True)
                await b.respond(datagram, True)
                await ready(second, b)
                await ready(datagram, b)
                assert len(a.connections) == 4 and len(b.connections) == 3 and len(c.connections) == 1
                await wait_until(lambda: len(directory.coord.sessions) == 8)
                assert len(directory.coord.allocations) == 4
                assert all(len(pair.participants) == 2 for pair in directory.consents.values())
                # The device limit includes pending requests, not only live data sockets.
                pending = [await request(control_port, 'b', p1) for _ in range(12)]
                await wait_until(lambda: len(b.requests) == 12)
                response = await http(control_port, '/api/requests', 'POST',
                    dict(peer='b', port=p1), token='t' * 64)
                assert response[0] == 400
                with pytest.raises(ValueError, match='connection limit'):
                    await directory.command(directory.online['a'], dict(kind='request_connection',
                        id=secrets.token_hex(16), peer='b', port=p1, protocol='tcp'))
                await cli(f'http://127.0.0.1:{control_port}', token, 'close', '--id', pending[0])
                await wait_until(lambda: pending[0] not in b.requests and pending[0] not in a.requests)
                for key in pending[1:]:
                    await b.respond(key, False)
                await wait_until(lambda: not a.requests and not b.requests)
                for mode in ('auto', 'relay-only', 'direct-only'):
                    await asyncio.gather(*(device.mode(mode) for device in devices))
                    await asyncio.gather(tcp_check(first, b'B1:'), tcp_check(second, b'B2:'), tcp_check(third, b'C:'))
                address = next(iter(a.connections[datagram].proxy.forwards.values()))['address']
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.setblocking(False)
                    loop = asyncio.get_running_loop()
                    await loop.sock_sendto(sock, b'test', tuple(address))
                    assert (await asyncio.wait_for(loop.sock_recvfrom(sock, 100), 5))[0] == b'UDP:test'

                await cli(f'http://127.0.0.1:{control_port}', token, 'close', '--id', first)
                await wait_until(lambda: first not in a.connections and first not in b.connections)
                assert len(directory.consents) == 3
                await asyncio.gather(tcp_check(second, b'B2:'), tcp_check(third, b'C:'))
                # A conflicting local listener fails only that newly approved instance.
                occupied = next(iter(a.connections[second].proxy.forwards.values()))['address'][1]
                failed = await request(control_port, 'b', p1, local_port=occupied)
                await wait_until(lambda: failed in b.requests)
                await b.respond(failed, True)
                await wait_until(lambda: a.error and failed in a.error)
                await wait_until(lambda: failed not in b.connections and not any(
                    pair.id == failed for pair in directory.consents.values()))
                await tcp_check(second, b'B2:')
                # Device-wide revocation closes B's instances, while A-C stays alive.
                from overlay.identity import revoke
                revoke(directory.coord.policy.path, 'b')
                await directory.reap()
                await wait_until(lambda: not b.connections and set(a.connections) == {third})
                await tcp_check(third, b'C:')
                await a.leave()
                await wait_until(lambda: not c.connections and not directory.consents
                    and not directory.coord.sessions)
        finally:
            await asyncio.gather(*(device.leave() for device in devices))
            udp.close()
            for listener in listeners:
                listener.close()
                await listener.wait_closed()
    from overlay.runtime import run
    run(scenario())
