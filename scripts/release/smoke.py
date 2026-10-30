"""Exercise extracted native packages, not checkout imports; no production state."""
import argparse
import http.cookiejar as cookiejar
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import shutil
import tarfile
import tempfile
import threading
import time
from urllib.request import Request, build_opener, HTTPCookieProcessor, HTTPSHandler, ProxyHandler
import zipfile


def port(kind=socket.SOCK_STREAM):
    with socket.socket(type=kind) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def wait(call, predicate=bool, timeout=25):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            value = call()
            if predicate(value):
                return value
        except (OSError, ValueError):
            pass
        time.sleep(.1)
    raise TimeoutError('Native package smoke condition timed out')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dist', type=Path)
    args = parser.parse_args()
    suffix = 'windows-x64.zip' if os.name == 'nt' else 'linux-x64.tar.gz'
    archive = next(args.dist.glob('p2p-overlay-client-*-' + suffix)).resolve()
    with tempfile.TemporaryDirectory(prefix='overlay-native-smoke-') as tmp:
        tmp = Path(tmp)
        if archive.suffix == '.zip':
            with zipfile.ZipFile(archive) as z:
                z.extractall(tmp / 'extracted')
        else:
            with tarfile.open(archive) as tar:
                tar.extractall(tmp / 'extracted', filter='data')
        folder = next((tmp / 'extracted').iterdir())
        exe = folder / ('p2p-overlay.exe' if os.name == 'nt' else 'p2p-overlay')
        if os.name != 'nt':
            exe.chmod(0o755)
        context = ssl.create_default_context()
        plain = build_opener(ProxyHandler({}), HTTPCookieProcessor(cookiejar.CookieJar()))
        def http(base, path, data=None, token=None, opener=plain):
            headers = {'Content-Type': 'application/json'}
            if token:
                headers['Authorization'] = 'Bearer ' + token
            request = Request(base + path, data=None if data is None else json.dumps(data).encode(), headers=headers)
            with opener.open(request, timeout=5) as r:
                raw = r.read()
                return json.loads(raw) if r.headers.get_content_type() == 'application/json' else raw
        env = dict(os.environ, PATH='', LOCALAPPDATA=str(tmp / 'state'), XDG_CONFIG_HOME=str(tmp / 'state'))
        # Prevent source shadowing and verify no system Python/OpenSSL is needed.
        env.pop('PYTHONPATH', None)
        processes, logs = [], []
        def launch(*arguments, executable=exe):
            log = (tmp / f'process-{len(processes)}.log').open('w')
            logs.append(log)
            p = subprocess.Popen([str(executable), *arguments], cwd=tmp, env=env, stdout=log, stderr=log)
            processes.append(p)
            return p
        listeners = []
        try:
            subprocess.run([str(exe), '--version'], cwd=tmp, env=env, check=True)
            subprocess.run([str(exe), 'doctor'], cwd=tmp, env=env, check=True)
            cert, server_key = tmp / 'server.crt', tmp / 'server.key'
            openssl = shutil.which('openssl', path=os.defpath)
            if openssl is None and os.name == 'nt':
                candidate = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Git/usr/bin/openssl.exe'
                openssl = str(candidate) if candidate.is_file() else None
            if openssl is None:
                raise RuntimeError('OpenSSL is required only to create the local smoke certificate')
            subprocess.run([openssl, 'req', '-x509', '-newkey', 'ed25519', '-nodes', '-days', '1',
                            '-keyout', str(server_key), '-out', str(cert), '-subj', '/CN=127.0.0.1',
                            '-addext', 'subjectAltName=IP:127.0.0.1'], check=True, capture_output=True)
            cp, up = port(), port(socket.SOCK_DGRAM)
            token = tmp / 'admin.token'
            launch('server', '--host', '127.0.0.1', '--control-port', str(cp), '--udp-port', str(up),
                   '--cert', str(cert), '--key', str(server_key), '--registry', str(tmp / 'members.json'),
                   '--admin-token-file', str(token))
            secure = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context(cafile=str(cert))))
            server = f'https://127.0.0.1:{cp}'
            wait(lambda: http(server, '/health', opener=secure))
            admin = token.read_text().strip()
            ap, bp = port(), port()
            configs = [tmp / 'a/client.json', tmp / 'b/client.json']
            for i, config in enumerate(configs):
                folder = config.parent
                folder.mkdir()
                identity_key, request = folder / 'identity.key', folder / 'identity.request.json'
                subprocess.run([str(exe), 'identity', '--node', 'release-' + str(i),
                                '--key', str(identity_key), '--request', str(request)],
                               cwd=tmp, env=env, check=True, stdout=subprocess.DEVNULL)
                config.write_text(json.dumps({'schema_version': 1, 'server': '127.0.0.1',
                    'control_port': cp, 'udp_port': up, 'node': 'release-' + str(i),
                    'identity': str(identity_key), 'ca': str(cert)}))
            launch('client', '--config', str(configs[0]), '--control-port', str(ap))
            launch('client', '--config', str(configs[1]), '--control-port', str(bp))
            bases = [f'http://127.0.0.1:{p}' for p in (ap, bp)]
            for base in bases:
                wait(lambda: http(base, '/'))
                assert http(base, '/ui/app.js')
            pending = wait(lambda: http(server, '/admin/api/status', token=admin, opener=secure)['pending'], lambda v: len(v) == 2)
            for entry in pending:
                http(server, '/admin/api/enrollments/' + entry['id'],
                     {'accept': True, 'fingerprint': entry['fingerprint']}, admin, secure)
            for base in bases:
                wait(lambda: http(base, '/api/status'), lambda s: s['state'] == 'online')
            tcp = socket.socket(); tcp.bind(('127.0.0.1', 0)); tcp.listen(); listeners.append(tcp)
            def tcp_echo():
                try:
                    c, _ = tcp.accept()
                    with c:
                        while data := c.recv(65536):
                            c.sendall(data)
                except OSError:
                    pass
            threading.Thread(target=tcp_echo, daemon=True).start()
            udp = socket.socket(type=socket.SOCK_DGRAM); udp.bind(('127.0.0.1', 0)); listeners.append(udp)
            def udp_echo():
                try:
                    data, source = udp.recvfrom(2048); udp.sendto(data, source)
                except OSError:
                    pass
            threading.Thread(target=udp_echo, daemon=True).start()
            for mode in ('direct-only', 'relay-only'):
                for base in bases:
                    http(base, '/api/mode', {'mode': mode})
                for protocol, target in [('tcp', tcp.getsockname()[1]), ('udp', udp.getsockname()[1])]:
                    if mode == 'relay-only':
                        threading.Thread(target=tcp_echo if protocol == 'tcp' else udp_echo, daemon=True).start()
                    request = http(bases[0], '/api/requests', {'peer': 'release-1', 'port': target, 'protocol': protocol})
                    wait(lambda: http(bases[1], '/api/status')['requests'])
                    http(bases[1], '/api/requests/' + request['id'], {'accept': True})
                    status = wait(lambda: http(bases[0], '/api/status')['connections'],
                                  lambda c: any(x['id'] == request['id'] and x.get('path') in ('direct', 'relay') for x in c))
                    record = next(x for x in status if x['id'] == request['id'])
                    # Forward address schema is owned by Proxy.status.
                    forwarding = record['forwards'][0]['address']
                    with socket.socket(type=socket.SOCK_STREAM if protocol == 'tcp' else socket.SOCK_DGRAM) as s:
                        s.settimeout(12)
                        if protocol == 'tcp':
                            s.connect(tuple(forwarding)); s.sendall(b'native-package-tcp'); assert s.recv(64) == b'native-package-tcp'
                        else:
                            s.sendto(b'native-package-udp', tuple(forwarding)); assert s.recv(64) == b'native-package-udp'
                    http(bases[0], '/api/disconnect', {})
            if os.name != 'nt':
                server_archive = next(args.dist.glob('p2p-overlay-server-*-linux-x64.tar.gz'))
                server_folder = tmp / 'server-package'
                server_folder.mkdir()
                with tarfile.open(server_archive) as tar:
                    tar.extractall(server_folder, filter='data')
                package = next(server_folder.iterdir())
                server_exe = package / 'bin/p2p-overlay'
                server_exe.chmod(0o755)
                subprocess.run([str(server_exe), 'doctor'], cwd=tmp, env=env, check=True,
                               stdout=subprocess.DEVNULL)
                server_cp, server_up = port(), port(socket.SOCK_DGRAM)
                launch('server', '--host', '127.0.0.1', '--control-port', str(server_cp),
                       '--udp-port', str(server_up), '--cert', str(cert), '--key', str(server_key),
                       '--registry', str(tmp / 'server-members.json'),
                       '--admin-token-file', str(tmp / 'server-admin.token'), executable=server_exe)
                server_tls = build_opener(ProxyHandler({}), HTTPSHandler(
                    context=ssl.create_default_context(cafile=str(cert))))
                wait(lambda: http(f'https://127.0.0.1:{server_cp}', '/health', opener=server_tls))
            print('Native package identity, HTTPS admission, browser assets, direct/relay TCP/UDP, and server startup passed')
        except BaseException:
            for log in logs:
                log.flush()
            for name in tmp.glob('process-*.log'):
                print(name.name, name.read_text(errors='replace')[-4000:])
            raise
        finally:
            for s in listeners:
                s.close()
            for p in processes:
                if p.poll() is None:
                    p.terminate()
                p.wait(timeout=15)
            for log in logs:
                log.close()


if __name__ == '__main__':
    main()
