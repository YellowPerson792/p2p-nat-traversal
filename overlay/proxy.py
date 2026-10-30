"""Application-independent forwarding scoped to one approved destination port.

TCP uses bounded per-channel credits above the existing recoverable message link.
UDP uses QUIC DATAGRAM and intentionally does not add reliability. The approved
requester can reach only the recipient's explicitly approved loopback port.
"""
import asyncio
from dataclasses import dataclass, field
import json
import secrets
import socket
import struct
import time

from . import client as transport
from .secure import MAX_RELIABLE_PAYLOAD
from .wire import MAX_PAYLOAD, log
from .network import bound_ipv4_socket

HEADER = struct.Struct('!2sB16s')
# Code 6 was the old proxy CLOSE record. Abort now uses the transport control
# stream, so it can bypass a stalled application stream. Keep UDP's code stable.
OPEN, READY, DATA, CREDIT, EOF, UDP = 1, 2, 3, 4, 5, 7
# At ~120 ms RTT this budget allows ~35 Mbps before the per-channel credit
# becomes limiting. Keep the proxy from reintroducing the old small-window cap.
WINDOW = 512 * 1024
CHUNK = MAX_RELIABLE_PAYLOAD - HEADER.size
UDP_LIMIT = MAX_PAYLOAD - HEADER.size
MAX_CHANNELS = 64
# Bound each batching/scheduling turn without delaying interactive traffic.
IO_BATCH = 128 * 1024


def frame(kind, key, body=b''):
    return HEADER.pack(b'P3', kind, key) + body



@dataclass
class Channel:
    key: bytes
    protocol: str
    allocation_id: object
    local: bool
    mapping: str | None = None
    writer: object = None
    datagram: object = None
    destination: object = None
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    writable: asyncio.Event = field(default_factory=asyncio.Event)
    inbox: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=WINDOW // CHUNK + 4))
    credit: int = 0
    allowance: int = WINDOW
    sent_eof: bool = False
    received_eof: bool = False
    drained_eof: bool = False
    closed: bool = False
    touched: float = field(default_factory=time.monotonic)
    tasks: set = field(default_factory=set)


class DatagramSocket:
    """Raw async UDP I/O preserves even empty datagrams on Python 3.12/Windows."""
    @classmethod
    async def create(cls, spawn, callback, local=('127.0.0.1', 0), remote=None):
        obj = cls()
        obj.sock = bound_ipv4_socket({'bind_host': local[0]}, socket.SOCK_DGRAM, local[1])
        obj.remote = remote
        try:
            if remote is not None:
                await asyncio.get_running_loop().sock_connect(obj.sock, remote)
        except BaseException:
            obj.sock.close()
            raise
        obj.task = spawn(obj.receive_loop(callback))
        return obj

    async def receive_loop(self, callback):
        loop = asyncio.get_running_loop()
        while True:
            try:
                data, addr = await loop.sock_recvfrom(self.sock, 65536)
                callback(data, addr)
            except OSError:
                await asyncio.sleep(.05)

    def sendto(self, data, destination=None):
        if self.remote is not None:
            self.sock.send(data)
        else:
            self.sock.sendto(data, destination)

    def get_extra_info(self, name):
        return self.sock.getsockname() if name == 'sockname' else self.sock

    def close(self):
        self.task.cancel()
        self.sock.close()


class Proxy:
    def __init__(self, config, node, peer, mode='auto', client=None):
        self.target_port, self.protocol = config.get('target_port'), config.get('protocol')
        self.initiator = config.get('initiator')
        if (type(self.target_port) is not int or not 1 <= self.target_port <= 65535
                or self.protocol not in ('tcp', 'udp') or type(self.initiator) is not bool):
            raise ValueError('An approved target port, protocol and direction are required')
        self.client = client or transport.Client(config, node, peer, mode)
        self.client.on_stream_data = self.receive_stream
        self.client.on_stream_reset = self.reset_stream
        self.client.on_datagram = self.receive_datagram
        self.client.on_reset = self.reset
        self.channels = {}
        self.udp_channels = {}
        self.forwards = {}
        self.tasks = set()
        self.dropped_datagrams = 0
        self.stopping = False
        self.forward_lock = asyncio.Lock()

    def spawn(self, coro, channel=None):
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        if channel:
            channel.tasks.add(task)
        def done(t):
            self.tasks.discard(t)
            if channel:
                channel.tasks.discard(t)
            if not t.cancelled():
                error = t.exception()
                if error:
                    log('proxy_task_error', error=type(error).__name__)
                    if channel:
                        self.drop(channel, notify=True)
        task.add_done_callback(done)
        return task

    def new_channel(self, protocol, local, key=None, mapping=None):
        if protocol != self.protocol or local != self.initiator:
            raise ValueError('Channel is outside the approved direction/protocol')
        if len(self.channels) >= MAX_CHANNELS:
            raise ConnectionError('Channel limit reached')
        if not self.client.ready.is_set() or self.client.allocation_id is None:
            raise ConnectionError('Peer is not connected')
        key = key or secrets.token_bytes(16)
        if key in self.channels:
            raise ValueError('Duplicate channel')
        if key == bytes(16):
            raise ValueError("Reserved channel key")
        self.client.open_stream(key)
        c = Channel(key, protocol, self.client.allocation_id, local, mapping)
        self.channels[key] = c
        return c

    async def send(self, c, kind, body=b''):
        if c.closed or c.allocation_id != self.client.allocation_id:
            raise ConnectionError('Channel ended')
        packet = frame(kind, c.key, body)
        # Most records fit immediately. Install a timeout only when backpressure
        # actually requires waiting; retain the transport's native buffer bounds.
        if not self.client.send(packet, reliable=True, stream=c.key):
            await asyncio.wait_for(self.client.send_reliable(packet, stream=c.key), 30)

    async def open(self, c):
        try:
            await self.send(c, OPEN, json.dumps({'protocol': c.protocol, 'port': self.target_port}).encode())
            await asyncio.wait_for(c.ready.wait(), 15)
            if c.closed:
                raise ConnectionError('Peer channel rejected')
        except Exception:
            self.drop(c, notify=True)
            raise

    def drop(self, c, notify=False, graceful=False):
        if c.closed:
            return
        c.closed = True
        self.channels.pop(c.key, None)
        if c.local and c.protocol == 'udp':
            self.udp_channels.pop((c.mapping, c.destination), None)
        self.client.close_stream(c.key, abort=not graceful, notify=notify)
        c.ready.set()
        c.writable.set()
        if c.writer:
            c.writer.close()
        if c.datagram:
            c.datagram.close()
        for task in list(c.tasks):
            if task is not asyncio.current_task():
                task.cancel()

    def reset_stream(self, key):
        channel = self.channels.get(key)
        if channel:
            self.drop(channel)

    def receive_stream(self, key, packet, path):
        # Bind the proxy record to its authenticated logical QUIC channel.
        # A peer cannot use one stream to address another application socket.
        if len(packet) < HEADER.size or HEADER.unpack_from(packet)[2] != key:
            self.client.close_stream(key, abort=True)
            return
        self.receive(packet, path)

    def reset(self):
        # Session replacement cannot transparently preserve local TCP sockets.
        for c in list(self.channels.values()):
            self.drop(c)

    def finish(self, c):
        if c.sent_eof and c.drained_eof:
            self.drop(c, graceful=True)

    async def read_tcp(self, c, reader):
        try:
            transferred = 0
            while not c.closed:
                c.writable.clear()
                if not c.credit:
                    await c.writable.wait()
                    continue
                data = await reader.read(min(CHUNK, c.credit))
                if not data:
                    await self.send(c, EOF)
                    c.sent_eof = True
                    self.finish(c)
                    return
                c.credit -= len(data)
                await self.send(c, DATA, data)
                transferred += len(data)
                if transferred >= IO_BATCH:
                    transferred = 0
                    await asyncio.sleep(0)
        except (OSError, ConnectionError, TimeoutError):
            self.drop(c, notify=True)

    async def write_tcp(self, c):
        try:
            while not c.closed:
                data = await c.inbox.get()
                if data is None:
                    c.writer.write_eof()
                    await c.writer.drain()
                    c.drained_eof = True
                    self.finish(c)
                    return
                # Consume only data already queued: never wait to fill a batch.
                # Keep both the work per turn and the extra socket buffering bounded.
                count = 0
                eof = False
                while True:
                    c.writer.write(data)
                    count += len(data)
                    if count >= IO_BATCH or c.inbox.empty():
                        break
                    data = c.inbox.get_nowait()
                    if data is None:
                        eof = True
                        break
                await c.writer.drain()
                # Credit is returned only after bounded local transport backpressure.
                c.allowance += count
                await self.send(c, CREDIT, count.to_bytes(4, 'big'))
                if eof:
                    c.writer.write_eof()
                    await c.writer.drain()
                    c.drained_eof = True
                    self.finish(c)
                    return
                await asyncio.sleep(0)
        except (OSError, ConnectionError, TimeoutError):
            self.drop(c, notify=True)

    async def accept_tcp(self, reader, writer, mapping):
        c = None
        try:
            c = self.new_channel('tcp', True, mapping=mapping)
            c.writer = writer
            await self.open(c)
            self.spawn(self.write_tcp(c), c)
            await self.read_tcp(c, reader)
        except (ValueError, OSError, ConnectionError, TimeoutError):
            writer.close()
            if c:
                self.drop(c, notify=True)

    async def accept_remote(self, c):
        try:
            if c.protocol == 'tcp':
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection('127.0.0.1', self.target_port), 10)
                if c.closed:
                    writer.close()
                    return
                c.writer = writer
            else:
                datagram = await DatagramSocket.create(self.spawn,
                    lambda data, addr: self.send_udp(c, data), local=('0.0.0.0', 0),
                    remote=('127.0.0.1', self.target_port))
                if c.closed:
                    datagram.close()
                    return
                c.datagram = datagram
            c.credit = WINDOW
            c.ready.set()
            c.writable.set()
            await self.send(c, READY)
            if c.protocol == 'tcp':
                self.spawn(self.write_tcp(c), c)
                self.spawn(self.read_tcp(c, reader), c)
        except (ValueError, OSError, ConnectionError, TimeoutError):
            self.drop(c, notify=True)

    def receive_datagram(self, packet, path):
        if len(packet) >= HEADER.size and packet[:3] == b'P3' + bytes([UDP]):
            self.receive(packet, path)

    def receive(self, packet, path):
        if len(packet) < HEADER.size:
            return
        magic, kind, key = HEADER.unpack_from(packet)
        if magic != b'P3':
            return
        body = packet[HEADER.size:]
        c = self.channels.get(key)
        try:
            if kind == OPEN:
                if c:
                    raise ValueError('Duplicate open')
                request = json.loads(body)
                if (set(request) != {'protocol', 'port'} or request['protocol'] != self.protocol
                        or type(request['port']) is not int or request['port'] != self.target_port
                        or self.initiator):
                    self.client.close_stream(key, abort=True)
                    return
                c = self.new_channel(request['protocol'], False, key)
                self.spawn(self.accept_remote(c), c)
                return
            if not c:
                return
            c.touched = time.monotonic()
            if kind == READY and c.local and not c.ready.is_set() and not body:
                c.credit = WINDOW
                c.ready.set()
                c.writable.set()
            elif kind == UDP and c.protocol == 'udp' and c.ready.is_set() and len(body) <= UDP_LIMIT:
                if c.local:
                    mapping = self.forwards.get(c.mapping)
                    if mapping:
                        mapping['listener'].sendto(body, c.destination)
                else:
                    c.datagram.sendto(body)
            elif kind == DATA and c.protocol == 'tcp' and c.ready.is_set() and not c.received_eof:
                if not body or len(body) > CHUNK or len(body) > c.allowance:
                    raise ValueError('Receive credit exceeded')
                c.allowance -= len(body)
                c.inbox.put_nowait(body)
            elif kind == CREDIT and c.protocol == 'tcp' and len(body) == 4:
                count = int.from_bytes(body, 'big')
                if not 0 < count <= WINDOW - c.credit:
                    raise ValueError('Invalid credit')
                c.credit += count
                c.writable.set()
            elif kind == EOF and c.protocol == 'tcp' and not body and not c.received_eof:
                c.received_eof = True
                c.inbox.put_nowait(None)
            else:
                raise ValueError('Unexpected proxy frame')
        except (ValueError, KeyError, TypeError, OSError, asyncio.QueueFull):
            if c:
                self.drop(c, notify=True)

    def send_udp(self, c, data):
        c.touched = time.monotonic()
        if c.closed or not c.ready.is_set() or len(data) > UDP_LIMIT or not self.client.send(
                frame(UDP, c.key, data), reliable=False):
            self.dropped_datagrams += 1

    def local_udp(self, mapping, data, addr):
        if len(data) > UDP_LIMIT:
            self.dropped_datagrams += 1
            return
        c = self.udp_channels.get((mapping['id'], addr))
        if c is None:
            try:
                c = self.new_channel('udp', True, mapping=mapping['id'])
                c.destination = addr
                self.udp_channels[(mapping['id'], addr)] = c
                self.spawn(self.first_udp(c, data), c)
            except ConnectionError:
                self.dropped_datagrams += 1
        else:
            self.send_udp(c, data)

    async def first_udp(self, c, data):
        try:
            await self.open(c)
            self.send_udp(c, data)
        except (ConnectionError, TimeoutError):
            pass

    async def add_forward(self, protocol, port=0):
        async with self.forward_lock:
            return await self._add_forward(protocol, port)

    async def _add_forward(self, protocol, port):
        if not self.initiator or protocol != self.protocol:
            raise ValueError('Only the requester may forward the approved protocol')
        if self.stopping or len(self.forwards) >= 32:
            raise ValueError('Forward limit reached or proxy stopping')
        if protocol not in ('tcp', 'udp') or type(port) is not int or not 0 <= port <= 65535:
            raise ValueError('Invalid protocol or local port')
        key = secrets.token_hex(8)
        mapping = dict(id=key, protocol=protocol)
        if protocol == 'tcp':
            listener = await asyncio.start_server(
                lambda r, w: self.spawn(self.accept_tcp(r, w, key)), '127.0.0.1', port)
            address = listener.sockets[0].getsockname()
        else:
            listener = await DatagramSocket.create(self.spawn,
                lambda data, addr: self.local_udp(mapping, data, addr), local=('127.0.0.1', port))
            address = listener.get_extra_info('sockname')
        mapping.update(listener=listener, address=list(address))
        self.forwards[key] = mapping
        return {k: v for k, v in mapping.items() if k != 'listener'}

    async def remove_forward(self, key):
        async with self.forward_lock:
            await self._remove_forward(key)

    async def _remove_forward(self, key):
        mapping = self.forwards.pop(key)
        mapping['listener'].close()
        if mapping['protocol'] == 'tcp':
            await mapping['listener'].wait_closed()
        for c in list(self.channels.values()):
            if c.mapping == key:
                self.drop(c, notify=True)

    def status(self):
        return dict(node=self.client.node, peer=self.client.peer_name, mode=self.client.mode,
                    path=self.client.path, connected=self.client.ready.is_set(),
                    target_port=self.target_port, protocol=self.protocol,
                    direction='outgoing' if self.initiator else 'incoming', channels=len(self.channels),
                    udp_payload_limit=UDP_LIMIT, dropped_datagrams=self.dropped_datagrams,
                    forwards=[{k: v for k, v in f.items() if k != 'listener'} for f in self.forwards.values()],
                    streams=len(self.client.streams),
                    relay_allocation_id=self.client.allocation_id,
                    quic_streams=len(self.client.link.send_streams) if self.client.link else 0,
                    quic_original_destination_cid=self.client.link.original_cid.hex() if self.client.link else None,
                    peer_path=self.client.link.peer_path if self.client.link else None,
                    metrics=self.client.metrics)

    async def maintain(self):
        while True:
            await asyncio.sleep(5)
            for c in list(self.channels.values()):
                if c.protocol == 'udp' and time.monotonic() - c.touched > 60:
                    self.drop(c, notify=True)

    async def close(self):
        self.stopping = True
        for key in list(self.forwards):
            await self.remove_forward(key)
        self.reset()
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*list(self.tasks), return_exceptions=True)
        await self.client.close()
