"""Device onboarding and consent lifecycle, shared by browser and HTTP CLI."""
import asyncio
import contextlib
import json
from pathlib import Path
import secrets
import socket
import ssl
from urllib.parse import urlsplit

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from .identity import initialize, public_der, fingerprint, proof, valid_node, write_json
from .management import credential_file
from .proxy import Proxy
from .connection import Connection
from .network import bound_ipv4_socket
from .config import load as load_config


class Device:
    def __init__(self, config_path):
        self.path = Path(config_path).resolve()
        self.config = load_config(self.path, missing_ok=True)
        self.config.pop('services', None)
        self.config.pop('forwards', None)
        self.config.pop('consent_ticket', None)
        self.config.pop('default_port', None)
        self.connections = {}
        self.directory_task = self.ws = None
        self.state, self.error = 'offline', None
        self.peers, self.requests = [], {}
        self.identity = None
        self.lock = asyncio.Lock()
        self.pair_lock = asyncio.Lock()
        self.send_lock = asyncio.Lock()

    async def load_existing(self):
        if self.config.get('identity') and self.config.get('node'):
            try:
                public = await asyncio.to_thread(public_der, self.config['identity'])
                self.identity = dict(node=self.config['node'], fingerprint=fingerprint(public.hex()))
            except (ValueError, OSError) as exc:
                self.error = str(exc)

    def offline_required(self):
        if self.directory_task and not self.directory_task.done():
            raise ValueError('Leave the server before changing identity or settings')

    def save(self):
        write_json(self.path, self.config)

    async def generate(self, node):
        async with self.lock:
            self.offline_required()
            if not valid_node(node):
                raise ValueError('Invalid device name')
            folder = self.path.parent / 'identities'
            folder.mkdir(parents=True, exist_ok=True)
            key = folder / (node + '-' + secrets.token_hex(6) + '.key')
            request = await asyncio.to_thread(initialize, node, key, key.with_suffix('.request.json'))
            self.config.update(node=node, identity=str(key))
            self.identity = dict(node=node, fingerprint=request['fingerprint'])
            self.save()
            return self.identity

    async def load_identity(self, node, path=None, key_pem=None):
        async with self.lock:
            self.offline_required()
            if not valid_node(node) or (path is None) == (key_pem is None):
                raise ValueError('Select one local key file and a valid device name')
            if key_pem is not None:
                from cryptography.hazmat.primitives import serialization
                from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
                key = serialization.load_pem_private_key(key_pem.encode(), password=None)
                if not isinstance(key, Ed25519PrivateKey):
                    raise ValueError('An unencrypted Ed25519 private key is required')
                folder = self.path.parent / 'identities'
                folder.mkdir(parents=True, exist_ok=True)
                selected = folder / (node + '-' + secrets.token_hex(6) + '.key')
                with selected.open('xb') as stream:
                    selected.chmod(0o600)
                    stream.write(key.private_bytes(serialization.Encoding.PEM,
                        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            else:
                selected = Path(path).resolve()
            public = await asyncio.to_thread(public_der, selected)
            self.config.update(node=node, identity=str(selected))
            self.identity = dict(node=node, fingerprint=fingerprint(public.hex()))
            self.save()
            return self.identity

    async def setup(self, server, ca_pem=None, ca_path=None):
        async with self.lock:
            self.offline_required()
            url = urlsplit(server if '://' in server else 'https://' + server)
            if url.scheme not in ('https', 'wss') or not url.hostname or url.username or url.password or url.path not in ('', '/') or url.query or url.fragment:
                raise ValueError('Enter an HTTPS server address, e.g. https://server.example:8443')
            if ca_pem is not None and ca_path is not None:
                raise ValueError('Select only one CA source')
            if ca_pem is not None:
                ssl.create_default_context(cadata=ca_pem)
                ca = self.path.parent / ('server-' + secrets.token_hex(6) + '.crt')
                ca.write_text(ca_pem)
                self.config['ca'] = str(ca)
            elif ca_path:
                ssl.create_default_context(cafile=ca_path)
                self.config['ca'] = str(Path(ca_path).resolve())
            self.config.update(server=url.hostname, control_port=url.port or 8443)
            self.config.pop('forward_port', None)
            self.save()
            return self.status()

    async def join(self):
        async with self.lock:
            if not self.config.get('server') or not self.identity:
                raise ValueError('Set the server address and generate or load an identity first')
            if self.directory_task and not self.directory_task.done():
                return
            self.state, self.error = 'connecting', None
            self.directory_task = asyncio.create_task(self.directory_loop())

    async def send(self, message):
        async with self.send_lock:
            if self.ws is None or self.state != 'online':
                raise ValueError('Device is not admitted and online')
            try:
                await asyncio.wait_for(self.ws.send(json.dumps(message)), 5)
            except (ConnectionClosed, TimeoutError) as exc:
                raise ValueError('Server disconnected; leave and join again') from exc

    async def heartbeat(self, ws):
        while True:
            await asyncio.sleep(5)
            await ws.send(json.dumps({'kind': 'heartbeat'}))

    async def directory_loop(self):
        heartbeat = None
        control_socket = None
        try:
            context = ssl.create_default_context(cafile=self.config.get('ca'))
            url = f"wss://{self.config['server']}:{self.config.get('control_port', 8443)}/directory"
            public = await asyncio.to_thread(public_der, self.config['identity'])
            options = {}
            if self.config.get('interface_index') is not None or self.config.get('bind_host', '0.0.0.0') != '0.0.0.0':
                control_socket = bound_ipv4_socket(self.config, socket.SOCK_STREAM)
                loop = asyncio.get_running_loop()
                addresses = await loop.getaddrinfo(self.config['server'], self.config.get('control_port', 8443),
                                                    family=socket.AF_INET, type=socket.SOCK_STREAM)
                await asyncio.wait_for(loop.sock_connect(control_socket, addresses[0][4]), 10)
                options['sock'] = control_socket
            async with connect(url, ssl=context, proxy=None, max_size=32768, open_timeout=10, **options) as ws:
                self.ws = ws
                await ws.send(json.dumps({'node': self.config['node'], 'public_key': public.hex()}))
                challenge = json.loads(await asyncio.wait_for(ws.recv(), 10))
                response = await asyncio.to_thread(proof, self.config['identity'], challenge,
                                                   self.config['node'], None, 'directory')
                await ws.send(json.dumps(response))
                async for raw in ws:
                    message = json.loads(raw)
                    kind = message['kind']
                    if kind == 'enrollment_pending':
                        self.state = 'awaiting_admin'
                    elif kind == 'directory_welcome':
                        self.state, self.error = 'online', None
                        heartbeat = asyncio.create_task(self.heartbeat(ws))
                    elif kind == 'directory':
                        self.peers = message['peers']
                    elif kind == 'connection_request':
                        self.requests[message['id']] = dict(message, direction='incoming', accepted=False)
                    elif kind == 'request_sent':
                        intent = self.requests.get(message['id'])
                        if intent and intent['direction'] == 'outgoing' and all(
                                intent[k] == message[k] for k in ('peer', 'port', 'protocol')):
                            intent['confirmed'] = True
                    elif kind == 'connection_approved':
                        consent = self.requests.pop(message['id'], None)
                        if (consent is None or consent['peer'] != message['peer']
                                or consent['fingerprint'] != message['fingerprint']
                                or any(consent[k] != message[k] for k in ('port', 'protocol', 'direction'))
                                or (consent['direction'] == 'incoming' and not consent['accepted'])
                                or (consent['direction'] == 'outgoing' and not consent.get('confirmed'))):
                            self.error = 'Unsolicited or changed connection approval rejected'
                            if consent is not None:
                                await self.send({'kind': 'disconnect', 'id': message['id']})
                        else:
                            await self.open_pair(message, consent.get('local_port', 0))
                    elif kind == 'request_finished':
                        self.requests.pop(message['id'], None)
                    elif kind == 'connection_closed':
                        self.requests.pop(message['id'], None)
                        await self.close_pair(message['id'])
                    elif kind == 'request_error':
                        self.error = message['error']
                        if message.get('operation') == 'request_connection':
                            self.requests.pop(message.get('id'), None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = f'{type(exc).__name__}: {exc}'
        finally:
            if heartbeat:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
            self.ws = None
            self.state = 'offline'
            self.peers, self.requests = [], {}
            await self.close_pair()
            if control_socket is not None:
                control_socket.close()

    async def open_pair(self, message, local_port=0):
        async with self.pair_lock:
            key = message['id']
            if key in self.connections:
                return
            # A broker-delivered fingerprint pins this session's identity; it is
            # not an independent out-of-band verification of the coordinator.
            pinned = self.config.get('peer_pins', {}).get(message['peer'],
                self.config.get('peer_fingerprint', message['fingerprint']))
            if pinned != message['fingerprint']:
                self.error = 'Independently pinned peer identity changed'
                await self.send({'kind': 'disconnect', 'id': key})
                return
            config = dict(self.config, consent_ticket=message['ticket'], peer_fingerprint=pinned,
                          target_port=message['port'], protocol=message['protocol'],
                          initiator=message['direction'] == 'outgoing', bind_port=0)
            candidate = Proxy(config, config['node'], message['peer'], config.get('mode', 'auto'))
            connection = Connection(key, candidate)
            try:
                await connection.start(local_port)
            except BaseException as exc:
                if isinstance(exc, asyncio.CancelledError):
                    raise
                self.error = f'Connection {key}: could not start forwarding: {type(exc).__name__}: {exc}'
                await self.send({'kind': 'disconnect', 'id': key})
                return
            self.connections[key] = connection
            self.error = None

    async def close_pair(self, key=None):
        async with self.pair_lock:
            keys = list(self.connections) if key is None else [key]
            for connection_id in keys:
                connection = self.connections.pop(connection_id, None)
                if connection:
                    await connection.close()

    async def request(self, peer, port, protocol='tcp', local_port=0):
        if type(port) is not int or not 1 <= port <= 65535 or protocol not in ('tcp', 'udp'):
            raise ValueError('A valid target port and protocol are required')
        if type(local_port) is not int or not 0 <= local_port <= 65535:
            raise ValueError('Invalid local forwarding port')
        selected = next((p for p in self.peers if p['node'] == peer and p.get('available', True)), None)
        if selected is None:
            raise ValueError('Peer is not available')
        async with self.lock:
            if len(self.requests) + len(self.connections) >= 16:
                raise ValueError('Device connection limit reached')
            key = secrets.token_hex(16)
            self.error = None
            self.requests[key] = dict(id=key, peer=peer, fingerprint=selected['fingerprint'],
                port=port, protocol=protocol, local_port=local_port, direction='outgoing', confirmed=False)
            try:
                await self.send({'kind': 'request_connection', 'id': key, 'peer': peer,
                                 'port': port, 'protocol': protocol})
            except BaseException:
                self.requests.pop(key, None)
                raise
            return key

    async def respond(self, key, accept):
        if self.requests.get(key, {}).get('direction') != 'incoming':
            raise ValueError('Incoming request not found')
        self.requests[key]['accepted'] = accept
        await self.send({'kind': 'respond_connection', 'id': key, 'accept': accept})

    async def disconnect(self, key=None):
        if key is not None and key not in self.connections and key not in self.requests:
            raise ValueError('Connection not found')
        await self.send({'kind': 'disconnect', 'id': key})
        if key is None:
            self.requests.clear()
        else:
            self.requests.pop(key, None)
        await self.close_pair(key)

    async def leave(self):
        async with self.lock:
            if self.directory_task:
                self.directory_task.cancel()
                await asyncio.gather(self.directory_task, return_exceptions=True)
                self.directory_task = None
            await self.close_pair()

    async def mode(self, value):
        if value not in ('auto', 'direct-only', 'relay-only'):
            raise ValueError('Invalid mode')
        self.config['mode'] = value
        for connection in self.connections.values():
            connection.proxy.client.mode = value
            connection.proxy.client.select_path()
        self.save()

    def status(self):
        server = None
        if self.config.get('server'):
            server = f"https://{self.config['server']}:{self.config.get('control_port', 8443)}"
        return dict(state=self.state, error=self.error, server=server, identity=self.identity,
                    mode=self.config.get('mode', 'auto'),
                    peers=self.peers,
                    requests=list(self.requests.values()),
                    connections=[connection.status() for connection in self.connections.values()])


async def run_device(args):
    import uvicorn
    from .control import create_app
    device = Device(args.config)
    await device.load_existing()
    token_path = args.control_token_file or device.config.get('control_token_file') or (str(args.config) + '.control-token')
    token = credential_file(token_path)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        port = args.control_port if args.control_port is not None else device.config.get('management_port', 8765)
        listener.bind(('127.0.0.1', port))
        listener.listen(128)
        port = listener.getsockname()[1]
        if device.config.get('server') and device.identity:
            await device.join()
        print(f'Client page: http://127.0.0.1:{port} ; CLI credential file: {token_path}', flush=True)
        await uvicorn.Server(uvicorn.Config(create_app(device, token), host='127.0.0.1',
            port=port, log_level='warning')).serve(sockets=[listener])
    finally:
        await device.leave()
        listener.close()
