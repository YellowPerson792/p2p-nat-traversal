import argparse
import asyncio
from contextlib import asynccontextmanager
from http.client import HTTPConnection
import json
from pathlib import Path
import socket
import ssl
import time

import pytest
import uvicorn
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from overlay.cli import add_parser, invoke
from overlay.control import create_app as control_app
from overlay.device import Device
from overlay.proxy import DatagramSocket
from overlay.server import create_app as server_app
from test_identity import certificate


@asynccontextmanager
async def serve(app, certificate=None):
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    options = dict(log_level='critical')
    if certificate:
        options.update(ssl_certfile=str(certificate[0]), ssl_keyfile=str(certificate[1]))
    runtime = uvicorn.Server(uvicorn.Config(app, **options))
    task = asyncio.create_task(runtime.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not runtime.started:
                if task.done():
                    await task
                await asyncio.sleep(.01)
        yield listener.getsockname()[1]
    finally:
        runtime.should_exit = True
        await asyncio.wait_for(task, 8)
        listener.close()


def single_proxy(device):
    return next(iter(device.connections.values())).proxy if device.connections else None


async def wait_until(predicate, timeout=12):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(.02)


async def http(port, path, method='GET', body=None, token=None, cookie=None, extra=None):
    def call():
        connection = HTTPConnection('127.0.0.1', port, timeout=10)
        headers = dict(extra or {})
        if token:
            headers['Authorization'] = 'Bearer ' + token
        if cookie:
            headers['Cookie'] = cookie
        if body is not None:
            headers['Content-Type'] = 'application/json'
        connection.request(method, path, json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse()
        data = response.read()
        result = response.status, data, dict(response.getheaders())
        connection.close()
        return result
    return await asyncio.to_thread(call)


async def cli(url, token_path, *arguments, ca=None, admin=False):
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    add_parser(commands)
    args = ['ctl', '--url', url, '--token-file', str(token_path)]
    if ca:
        args.extend(['--ca', str(ca)])
    if admin:
        args.append('--admin')
    args.extend(arguments)
    return await asyncio.to_thread(invoke, parser.parse_args(args))


def test_browser_and_cli_share_port_scoped_consent(tmp_path, certificate):
    async def run():
        async def echo(reader, writer):
            try:
                while data := await reader.read(16384):
                    writer.write(data)
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
        # Windows may reserve a port for UDP even when TCP can allocate it.
        # Choose a fixture port usable by both independent application servers.
        for _ in range(20):
            target = await asyncio.start_server(echo, '127.0.0.1', 0)
            target_port = target.sockets[0].getsockname()[1]
            try:
                udp = await DatagramSocket.create(asyncio.create_task,
                    lambda data, addr: udp.sendto(data, addr), local=('127.0.0.1', target_port))
                break
            except OSError:
                target.close()
                await target.wait_closed()
        else:
            raise RuntimeError('No shared TCP/UDP loopback port available for the test')
        app = server_app(tmp_path / 'members.json', '127.0.0.1', 0, admin_token='a' * 64)
        directory = app.state.coordinator.directory
        a, b = Device(tmp_path / 'a/client.json'), Device(tmp_path / 'b/client.json')
        atoken, btoken, adminfile = tmp_path / 'a-token', tmp_path / 'b-token', tmp_path / 'admin-token'
        atoken.write_text('b' * 64)
        btoken.write_text('c' * 64)
        adminfile.write_text('a' * 64)
        try:
            async with serve(app, certificate) as server_port, \
                    serve(control_app(a, atoken.read_text())) as aport, \
                    serve(control_app(b, btoken.read_text())) as bport:
                aurl, burl, surl = f'http://127.0.0.1:{aport}', f'http://127.0.0.1:{bport}', f'https://127.0.0.1:{server_port}'
                assert (await http(aport, '/api/status'))[0] == 401
                page = await http(bport, '/')
                cookie = page[2]['set-cookie'].split(';')[0]
                other_cookie = (await http(aport, '/'))[2]['set-cookie'].split(';')[0]
                assert cookie.split('=', 1)[0] != other_cookie.split('=', 1)[0]
                browser_cookies = cookie + '; ' + other_cookie
                assert (await http(aport, '/api/status', cookie=browser_cookies))[0] == 200
                assert (await http(bport, '/api/status', cookie=browser_cookies))[0] == 200
                assert b'P2P device connections' in page[1]
                assert b'<script src="/ui/app.js" defer>' in page[1]
                assert b'<script>' not in page[1] and b'<style>' not in page[1]
                assert 'unsafe-inline' not in page[2]['content-security-policy']
                for asset, media in [('app.js', 'text/javascript'), ('style.css', 'text/css')]:
                    response = await http(bport, '/ui/' + asset, cookie=cookie)
                    assert response[0] == 200 and media in response[2]['content-type']
                assert 'HttpOnly' in page[2]['set-cookie'] and 'SameSite=strict' in page[2]['set-cookie']
                assert (await http(bport, '/api/status', cookie=cookie))[0] == 200
                assert (await http(bport, '/api/status', cookie=cookie, extra={'Host': 'evil.test'}))[0] == 403
                assert (await http(bport, '/api/join', 'POST', cookie=cookie,
                    extra={'Origin': 'http://evil.test'}))[0] == 403
                await cli(aurl, atoken, 'identity-generate', '--node', 'a')
                # Browser loads a file; the CLI generates locally via the same API.
                from overlay.identity import initialize
                key = tmp_path / 'existing.key'
                initialize('b', key, tmp_path / 'existing.request.json')
                assert (await http(bport, '/api/identity/load', 'POST',
                    {'node': 'b', 'key_pem': key.read_text()}, cookie=cookie))[0] == 200
                await cli(aurl, atoken, 'setup', '--server', surl, '--server-ca', str(certificate[0]))
                assert (await http(bport, '/api/setup', 'POST', dict(server=surl,
                    ca_pem=certificate[0].read_text()), cookie=cookie))[0] == 200
                await cli(aurl, atoken, 'join')
                assert (await http(bport, '/api/join', 'POST', cookie=cookie))[0] == 200
                await wait_until(lambda: a.state == b.state == 'awaiting_admin')
                assert not directory.online and single_proxy(a) is None and single_proxy(b) is None
                assert not a.peers and not b.peers
                for entry_id, entry in list(directory.pending.items()):
                    with pytest.raises(ValueError, match='HTTP 400'):
                        await cli(surl, adminfile, 'enrollment', '--request', entry_id, '--accept',
                            '--fingerprint', '0' * 64, admin=True, ca=certificate[0])
                    await cli(surl, adminfile, 'enrollment', '--request', entry_id, '--accept',
                        '--fingerprint', entry['request']['fingerprint'], admin=True, ca=certificate[0])
                await wait_until(lambda: a.state == b.state == 'online' and len(a.peers) == 1 and len(b.peers) == 1)
                assert (await http(bport, '/api/requests', 'POST', {'peer': 'a'}, cookie=cookie))[0] == 422
                for invalid_port in (-1, 65536, True):
                    assert (await http(bport, '/api/requests', 'POST',
                        {'peer': 'a', 'port': target_port, 'local_port': invalid_port}, cookie=cookie))[0] == 422
                assert (await cli(aurl, atoken, 'peers'))[0]['node'] == 'b'
                with pytest.raises(ValueError, match='HTTP 401'):
                    await cli(surl, atoken, 'status', admin=True, ca=certificate[0])
                # A broker message alone cannot substitute for local user intent.
                await directory.send(directory.online['a'], dict(kind='connection_approved',
                    id='unsolicited', peer='b', fingerprint=b.identity['fingerprint'], ticket='f' * 64))
                await wait_until(lambda: a.error == 'Unsolicited or changed connection approval rejected')
                assert single_proxy(a) is None
                # Even an admitted identity cannot open the pair channel without consent.
                tls = ssl.create_default_context(cafile=str(certificate[0]))
                async with connect(f'wss://127.0.0.1:{server_port}/ws', ssl=tls, proxy=None) as ws:
                    await ws.send(json.dumps({'node': 'a', 'peer': 'b'}))
                    with pytest.raises(ConnectionClosed):
                        await ws.recv()
                async with connect(f'wss://127.0.0.1:{server_port}/ws', ssl=tls, proxy=None) as ws:
                    await ws.send(json.dumps({'node': 'a', 'peer': None, 'purpose': 'directory'}))
                    with pytest.raises(ConnectionClosed):
                        await ws.recv()
                await cli(aurl, atoken, 'request', '--peer', 'b', '--port', str(target_port))
                await wait_until(lambda: bool(b.requests) and bool(a.requests))
                request_id = next(iter(b.requests))
                assert not directory.consents and single_proxy(a) is None and single_proxy(b) is None
                with pytest.raises(ValueError, match='Only the requested device'):
                    await directory.command(directory.online['a'],
                        dict(kind='respond_connection', id=request_id, accept=True))
                with pytest.raises(ValueError, match='HTTP 400'):
                    await cli(aurl, atoken, 'respond', '--request', request_id, '--accept')
                # The broker cannot change the port after local user intent was recorded.
                await directory.send(directory.online['a'], dict(kind='connection_approved',
                    id=request_id, peer='b', fingerprint=b.identity['fingerprint'], ticket='f' * 64,
                    port=target_port + 1 if target_port < 65535 else 1, protocol='tcp', direction='outgoing'))
                await wait_until(lambda: not a.requests and not b.requests)
                assert single_proxy(a) is None and single_proxy(b) is None and not directory.consents
                await cli(aurl, atoken, 'request', '--peer', 'b', '--port', str(target_port))
                await wait_until(lambda: bool(b.requests) and bool(a.requests))
                request_id = next(iter(b.requests))
                # Expired consent requests cannot start a connection.
                directory.requests[request_id]['expires'] = time.monotonic() - 1
                await directory.reap()
                await wait_until(lambda: not a.requests and not b.requests)
                assert single_proxy(a) is None and single_proxy(b) is None
                await cli(aurl, atoken, 'request', '--peer', 'b', '--port', str(target_port))
                await wait_until(lambda: bool(b.requests))
                request_id = next(iter(b.requests))
                assert (await http(bport, '/api/requests/' + request_id, 'POST', {'accept': False}, cookie=cookie))[0] == 202
                await wait_until(lambda: not a.requests and not b.requests)
                with socket.socket() as probe:
                    probe.bind(('127.0.0.1', 0))
                    local_port = probe.getsockname()[1]
                await cli(aurl, atoken, 'request', '--peer', 'b', '--port', str(target_port),
                          '--local-port', str(local_port))
                await wait_until(lambda: bool(b.requests) and bool(a.requests))
                request_id = next(iter(b.requests))
                assert a.requests[request_id]['local_port'] == local_port
                assert 'local_port' not in b.requests[request_id]
                assert (await http(bport, '/api/requests/' + request_id, 'POST', {'accept': True}, cookie=cookie))[0] == 202
                try:
                    await wait_until(lambda: single_proxy(a) is not None and single_proxy(b) is not None
                        and single_proxy(a).client.ready.is_set() and single_proxy(b).client.ready.is_set())
                except TimeoutError as exc:
                    raise AssertionError({'a': a.status(), 'b': b.status(),
                                          'consents': len(directory.consents)}) from exc
                assert len(single_proxy(a).forwards) == 1 and not single_proxy(b).forwards
                assert single_proxy(a).protocol == single_proxy(b).protocol == 'tcp'
                assert single_proxy(a).target_port == single_proxy(b).target_port == target_port
                forward = next(f for f in single_proxy(a).forwards.values() if f['protocol'] == 'tcp')['address']
                assert forward[1] == local_port
                reader, writer = await asyncio.open_connection(*forward)
                for mode in ('auto', 'relay-only', 'direct-only'):
                    await cli(aurl, atoken, 'mode', mode)
                    await cli(burl, btoken, 'mode', mode)
                    payload = bytes(range(256)) * 400
                    writer.write(payload)
                    await writer.drain()
                    assert await asyncio.wait_for(reader.readexactly(len(payload)), 12) == payload
                writer.write_eof()
                assert await asyncio.wait_for(reader.read(), 5) == b''
                writer.close()
                await writer.wait_closed()
                await cli(aurl, atoken, 'disconnect')
                await wait_until(lambda: single_proxy(a) is None and single_proxy(b) is None)
                await cli(aurl, atoken, 'request', '--peer', 'b', '--port', str(target_port), '--protocol', 'udp')
                await wait_until(lambda: bool(b.requests))
                request_id = next(iter(b.requests))
                assert b.requests[request_id]['port'] == target_port
                assert b.requests[request_id]['protocol'] == 'udp'
                assert (await http(bport, '/api/requests/' + request_id, 'POST', {'accept': True}, cookie=cookie))[0] == 202
                await wait_until(lambda: single_proxy(a) is not None and single_proxy(b) is not None
                    and single_proxy(a).client.ready.is_set() and single_proxy(b).client.ready.is_set())
                assert len(single_proxy(a).forwards) == 1 and not single_proxy(b).forwards
                forward = next(iter(single_proxy(a).forwards.values()))['address']
                with pytest.raises(ValueError):
                    single_proxy(b).new_channel('udp', True)
                with pytest.raises(ValueError):
                    single_proxy(a).new_channel('tcp', True)
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setblocking(False)
                try:
                    loop = asyncio.get_running_loop()
                    await loop.sock_sendto(sock, b'datagram', tuple(forward))
                    assert (await asyncio.wait_for(loop.sock_recvfrom(sock, 100), 5))[0] == b'datagram'
                finally:
                    sock.close()
                # Old service publication endpoints have been removed.
                assert (await http(bport, '/api/services', 'POST', {}, cookie=cookie))[0] == 404
                with pytest.raises(ValueError, match='HTTP 400'):
                    await cli(aurl, atoken, 'identity-generate', '--node', 'replacement')
                await cli(surl, adminfile, 'revoke', '--node', 'b', ca=certificate[0], admin=True)
                await wait_until(lambda: not directory.consents and single_proxy(a) is None and single_proxy(b) is None)
                assert not directory.coord.sessions
                state = await cli(surl, adminfile, 'status', admin=True, ca=certificate[0])
                assert not state['members']['b']['enabled']
                assert key.read_text() not in json.dumps(state)
        finally:
            await a.leave()
            await b.leave()
            udp.close()
            target.close()
            await target.wait_closed()
    from overlay.runtime import run as run_async
    run_async(run())


def test_directory_disconnect_removes_pending_enrollment(tmp_path, certificate):
    async def scenario():
        app = server_app(tmp_path / 'registry.json', '127.0.0.1', 0, admin_token='z' * 64)
        device = Device(tmp_path / 'client.json')
        async with serve(app, certificate) as port:
            await device.generate('new-device')
            await device.setup(f'https://127.0.0.1:{port}', ca_path=str(certificate[0]))
            await device.join()
            await wait_until(lambda: device.state == 'awaiting_admin')
            assert len(app.state.coordinator.directory.pending) == 1
            await device.leave()
            await wait_until(lambda: not app.state.coordinator.directory.pending)
            assert device.state == 'offline'
    asyncio.run(scenario())


def test_cancelled_pair_start_cleans_partial_proxy(tmp_path, monkeypatch):
    async def scenario():
        started = asyncio.Event()
        candidates = []
        class PartialProxy:
            def __init__(self, *args):
                self.client = self
                self.closed = False
                candidates.append(self)
            async def start(self):
                started.set()
                await asyncio.Event().wait()
            async def close(self):
                self.closed = True
        monkeypatch.setattr('overlay.device.Proxy', PartialProxy)
        device = Device(tmp_path / 'client.json')
        device.config.update(node='a')
        task = asyncio.create_task(device.open_pair(dict(id='2' * 32, peer='b', fingerprint='0' * 64, ticket='1' * 64, port=5000, protocol='tcp', direction='outgoing')))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert candidates[0].closed and single_proxy(device) is None
    asyncio.run(scenario())

def test_admin_frontend_assets_and_api_authentication(tmp_path):
    async def run():
        registry = tmp_path / 'members.json'
        registry.write_text('{"version":1,"members":{}}')
        async with serve(server_app(registry, udp_host='127.0.0.1', udp_port=0,
                                    admin_token='a' * 64)) as port:
            page = await http(port, '/admin')
            assert page[0] == 200
            assert b'<script src="/admin/ui/app.js" defer>' in page[1]
            assert b'<script>' not in page[1] and b'<style>' not in page[1]
            assert 'unsafe-inline' not in page[2]['content-security-policy']
            for asset, media in [('app.js', 'text/javascript'), ('style.css', 'text/css')]:
                response = await http(port, '/admin/ui/' + asset)
                assert response[0] == 200 and media in response[2]['content-type']
            assert (await http(port, '/admin/api/status'))[0] == 401
            assert (await http(port, '/admin/api/status', token='a' * 64))[0] == 200
            assert (await http(port, '/admin/api/status', token='a' * 64,
                               extra={'Origin': 'https://evil.test'}))[0] == 403
            assert (await http(port, '/admin/ui/server.key'))[0] == 404
    asyncio.run(run())
