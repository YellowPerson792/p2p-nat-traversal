# 应用层 P2P overlay：在现有 IP 网络之上，用节点名和自定义消息建立通信。
# 本文件传输应用消息，不创建 TUN 网卡，也不承载任意 IP 包。
#
# 建链流程：TLS/WebSocket 获取会话 -> UDP 注册并发现公网端点 -> 双方 UDP 探测
#          -> QUIC 握手并认证身份；auto 模式失败时经服务器中继，并定期重试直连。
# 控制平面负责身份、配对和地址交换；数据平面负责探测及应用消息传输。
# 可以把服务器理解成“交换地址的联系人 + 备用转发站”：双方先向它报到，
# 拿到彼此地址后尝试自己通信，失败时才让它转发应用消息。
# 控制连接用 TLS；节点之间用 QUIC/TLS 加密，直连和中继共用一个端到端 QUIC 连接。
import asyncio
import contextlib
import json
import socket
import ssl
import time

from websockets.asyncio.client import connect

from .wire import (RELAY_MAGIC, MAX_PAYLOAD, ReplayWindow, direct_packet, read_direct, relay_packet, read_relay, log, pack, unpack)
from .identity import proof, fingerprint
from .network import bound_ipv4_socket
from .streams import (ChannelState, DEFAULT_STREAM, MAX_STREAMS, MAX_STREAMS_PER_ALLOCATION,
    MAX_PENDING_BYTES, MAX_TOTAL_BYTES)
from .secure import (TransportIdentity, PeerLink,
    MAX_RELIABLE_PAYLOAD, DATA, RESET, verify_binding, is_initial)


class Client(asyncio.DatagramProtocol):
    """One authorized pair, one QUIC connection, multiple physical carriers."""
    def __init__(self, config, node, peer, mode="auto", on_data=None, on_reset=None):
        self.config, self.node, self.peer_name, self.mode = config, node, peer, mode
        self.on_data = on_data
        self.on_datagram = None
        self.on_stream_data = self.on_stream_reset = None
        self.on_reset = on_reset
        self.drop_peer_udp = config.get("drop_peer_udp", False)
        self.server = (config["server"], config.get("udp_port", 3478))
        self.sid = self.key = self.allocation_id = self.relay_side = self.peer_endpoint = None
        self.transport = self.ws = None
        self.credentials = self.binding = self.peer_binding = self.peer_identity = None
        self.link = None
        self.path = "waiting"
        self.outer_seq = 0
        self.server_replay = ReplayWindow()
        self.streams, self.retired_streams = {}, set()
        self.stream_count = 0
        self.writable = asyncio.Event()
        self.next_hole = self.next_register = 0
        self.hole_attempts = 0
        self.session_ttl = 20
        self.last_registration_reply = 0
        self.wakeup = asyncio.Event()
        self.control_wakeup = asyncio.Event()
        self.control_close_requested = False
        self.ready = asyncio.Event()
        self.stopping = False
        self.started = time.perf_counter()
        self.metrics = {"sent": 0, "received": 0, "direct_received": 0, "relay_received": 0,
            "invalid_packets": 0, "path_changes": [],
            "local_endpoint": None, "observed_endpoint": None, "observed_endpoints": [],
            "peer_endpoint": None, "peer_packets_dropped": 0, "probes_sent": 0,
            "probe_responses": 0, "unexpected_source_packets": 0, "udp_errors": 0,
            "interface_index": config.get("interface_index"), "control_endpoint": None,
            "failed_pending": 0}
        self.tasks = set()

    def wake(self):
        self.wakeup.set()

    def request_control_close(self):
        self.control_close_requested = True
        self.control_wakeup.set()

    def connection_made(self, transport):
        self.transport = transport
        self.metrics["local_endpoint"] = list(transport.get_extra_info("sockname"))

    def set_path(self, path, reason):
        was_ready = self.ready.is_set()
        previous = self.path
        if self.path != path:
            self.path = path
            event = {"path": path, "reason": reason, "elapsed": time.perf_counter() - self.started}
            self.metrics["path_changes"].append(event)
            log("path_changed", node=self.node, **event)
        link = self.link
        if link and link.available and path in ("direct", "relay"):
            self.ready.set()
            if previous != path or not was_ready:
                self.writable.set()
        else:
            self.ready.clear()

    def select_path(self):
        link = self.link
        if not link or not link.authenticated or link.closed:
            path = 'probing' if self.allocation_id else 'waiting'
        elif self.mode == 'direct-only':
            path = 'direct'
        elif self.mode == 'relay-only':
            path = 'relay'
        else:
            now = time.perf_counter()
            if link.path_healthy('direct', now):
                path = 'direct'
            elif link.path_healthy('relay', now):
                path = 'relay'
            else:
                # Keep retransmitting on the fallback carrier while probes run.
                path = 'relay'
        self.set_path(path, 'shared_quic_carrier')
        if link and path in ('direct', 'relay') and link.path != path:
            link.path = path
            link.next_probe[path] = 0
            link.publish_path()
        self.wake()  # Includes mode changes which keep the same selected path.

    def server_send(self, kind, **fields):
        if self.key:
            self.outer_seq += 1
            self.transport.sendto(pack({"kind": kind, "sid": self.sid,
                "seq": self.outer_seq, **fields}, self.key), self.server)

    def emit(self, path, packet):
        if path == "direct":
            if self.peer_endpoint:
                self.transport.sendto(direct_packet(self.allocation_id, packet), self.peer_endpoint)
        elif self.key:
            self.outer_seq += 1
            self.transport.sendto(relay_packet(self.allocation_id, self.relay_side, self.key, self.outer_seq, packet), self.server)

    def error_received(self, exc):
        self.metrics["udp_errors"] += 1
        log("udp_error", node=self.node, error=type(exc).__name__)

    def datagram_received(self, data, addr):
        try:
            if addr == self.server:
                if not self.key:
                    return
                if data.startswith(RELAY_MAGIC):
                    allocation_id, side, seq, packet = read_relay(data, self.key)
                    if (allocation_id != self.allocation_id or side != 1 - self.relay_side
                            or not self.server_replay.accept(seq)):
                        raise ValueError("Wrong or repeated relay envelope")
                    path = "relay"
                else:
                    message = unpack(data, self.key)
                    if message["kind"] != "registered":
                        return
                    self.last_registration_reply = time.perf_counter()
                    self.wake()
                    endpoint = message["endpoint"]
                    if endpoint != self.metrics["observed_endpoint"]:
                        self.metrics["observed_endpoint"] = endpoint
                        self.metrics["observed_endpoints"].append(endpoint)
                        log("nat_mapping_observed", node=self.node, public_endpoint=endpoint)
                    return
            else:
                if addr != self.peer_endpoint:
                    self.metrics["unexpected_source_packets"] += 1
                    return
                if self.drop_peer_udp:
                    self.metrics["peer_packets_dropped"] += 1
                    return
                allocation_id, packet = read_direct(data)
                path = "direct"
            if allocation_id != self.allocation_id or not self.peer_binding:
                raise ValueError("Stale peer allocation_id")
            if not packet:  # 仅打开 NAT 出站状态，不作为认证或直连成功的证据。
                return
            if (path == "direct" and self.mode == "relay-only") or (path == "relay" and self.mode == "direct-only"):
                return
            link = self.link
            if not link:
                if self.node < self.peer_name or not is_initial(packet):
                    return
                link = PeerLink(self, path, initial=packet)
                self.link = link
            # The envelope selects the authorized allocation_id; QUIC itself checks its
            # active destination CIDs and authenticates packets on both carriers.
            if link.closed:
                return
            link.receive(packet, path)
            self.select_path()
        except (ValueError, KeyError, TypeError, AttributeError, UnicodeError):
            self.metrics["invalid_packets"] += 1

    @property
    def pending_bytes(self):
        return self.link.pending_bytes() if self.link else 0

    @property
    def pending_count(self):
        """Number of native QUIC send buffers awaiting ACK, not message count."""
        return self.link.pending_count() if self.link else 0

    def open_stream(self, key=DEFAULT_STREAM):
        if not isinstance(key, bytes) or len(key) != 16:
            raise ValueError('A 16-byte logical stream key is required')
        if key in self.retired_streams:
            raise ConnectionError('Logical stream ended')
        stream = self.streams.get(key)
        if stream is None:
            if len(self.streams) >= MAX_STREAMS or self.stream_count >= MAX_STREAMS_PER_ALLOCATION:
                raise ConnectionError('Logical stream limit reached')
            stream = self.streams[key] = ChannelState(key)
            self.stream_count += 1
        return stream

    def send(self, payload, reliable=None, stream=DEFAULT_STREAM):
        reliable = self.config.get("delivery", "reliable") == "reliable" if reliable is None else reliable
        limit = MAX_RELIABLE_PAYLOAD if reliable else MAX_PAYLOAD
        if len(payload) > limit:
            raise ValueError(f"payload exceeds {limit} bytes")
        link = self.link
        if not link or not link.available:
            return False
        if reliable:
            state = self.open_stream(stream)
            if state.local_closed:
                raise ConnectionError('Logical stream ended')
            # aioquic's low-level API has no drain(): bound its native send
            # buffers instead of maintaining another ACK/retransmission layer.
            size = len(payload) + 5 + (21 if stream not in link.send_streams else 0)
            if (link.pending_bytes(stream) + size > MAX_PENDING_BYTES
                    or self.pending_bytes + size > MAX_TOTAL_BYTES):
                return False
            link.frame(DATA, payload, channel=stream)
            link.flush()
        else:
            if link.datagrams_pending() >= 64:
                return False
            link.quic.send_datagram_frame(b"\x00" + payload)
            link.flush()
        link.note_activity(time.perf_counter())
        self.metrics["sent"] += 1
        return True

    async def send_reliable(self, payload, stream=DEFAULT_STREAM):
        allocation_id = self.allocation_id
        state = self.open_stream(stream)
        while True:
            if self.stopping or self.allocation_id != allocation_id or self.streams.get(stream) is not state or state.local_closed:
                raise ConnectionError("Peer channel ended while waiting to send")
            self.writable.clear()
            if self.send(payload, reliable=True, stream=stream):
                return
            await self.writable.wait()

    def close_stream(self, key, abort=False, notify=True):
        state = self.streams.get(key)
        if state is None:
            if abort and key not in self.retired_streams:
                if self.stream_count >= MAX_STREAMS_PER_ALLOCATION:
                    raise ValueError('Closed stream limit reached')
                self.stream_count += 1
                self.retired_streams.add(key)
            return
        if abort:
            if notify:
                if self.link and self.link.available:
                    self.link.frame(RESET, key)
                    self.link.flush()
            self.retire_stream(key, abort=True)
        else:
            state.local_closed = True
            self.finish_stream(key)

    def finish_stream(self, key):
        state = self.streams.get(key)
        if not state or not state.local_closed:
            return
        if self.link and not self.link.closed:
            self.link.finish_stream(key)
        if state.remote_closed:
            self.retire_stream(key)

    def stream_ended(self, key):
        state = self.streams.get(key)
        if state:
            state.remote_closed = True
            self.finish_stream(key)

    def retire_stream(self, key, abort=False):
        state = self.streams.pop(key, None)
        if state is None:
            return
        self.retired_streams.add(key)
        self.writable.set()
        if self.link:
            self.link.retire_stream(key, abort=abort)
        if abort and self.on_stream_reset:
            self.on_stream_reset(key)

    def deliver(self, payload, path, reliable=True, stream=DEFAULT_STREAM):
        if self.link:
            self.link.note_activity(time.perf_counter())
        self.metrics["received"] += 1
        self.metrics[path + "_received"] += 1
        if reliable and self.on_stream_data:
            self.on_stream_data(stream, payload, path)
            return
        callback = self.on_data if reliable else (self.on_datagram or self.on_data)
        if callback:
            callback(payload, path)

    def reset_peer(self):
        self.metrics["failed_pending"] += self.pending_count
        if self.on_reset:
            self.on_reset()
        if self.link:
            self.link.close()
        self.link = None
        self.streams.clear()
        self.retired_streams.clear()
        self.stream_count = 0
        self.writable.set()
        self.allocation_id = self.relay_side = self.peer_endpoint = self.peer_binding = self.peer_identity = None
        self.next_hole = self.hole_attempts = 0
        self.set_path("waiting", "peer_unavailable")
        self.wake()

    async def control_loop(self):
        context = ssl.create_default_context(cafile=self.config.get("ca"))
        url = f'wss://{self.config["server"]}:{self.config.get("control_port", 8443)}/ws'
        while not self.stopping:
            control_sock = None
            heartbeat = None
            try:
                options = {"proxy": None}
                if self.config.get("interface_index") is not None or self.config.get("bind_host", "0.0.0.0") != "0.0.0.0":
                    control_sock = bound_ipv4_socket(self.config, socket.SOCK_STREAM)
                    await asyncio.wait_for(asyncio.get_running_loop().sock_connect(control_sock,
                        (self.server[0], self.config.get("control_port", 8443))), 10)
                    options["sock"] = control_sock
                self.credentials = await asyncio.to_thread(TransportIdentity, self.config["identity"])
                async with connect(url, ssl=context, max_size=16384, **options) as ws:
                    self.ws = ws
                    self.control_close_requested = False
                    self.control_wakeup.clear()
                    heartbeat = asyncio.create_task(self.control_maintenance(ws))
                    self.metrics["control_endpoint"] = list(ws.local_address)
                    await ws.send(json.dumps({"node": self.node, "peer": self.peer_name,
                                              "ticket": self.config.get('consent_ticket')}))
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg["kind"] == "challenge":
                            response = await asyncio.to_thread(proof, self.config["identity"], msg,
                                self.node, self.peer_name, "connect")
                            await ws.send(json.dumps(response))
                        elif msg["kind"] == "welcome":
                            self.sid, self.key = msg["sid"], msg["key"]
                            self.session_ttl = float(msg['ttl'])
                            self.last_registration_reply = 0
                            self.next_register = time.perf_counter() + min(1, self.session_ttl / 3)
                            self.wake()
                            self.server_replay = ReplayWindow()
                            self.server = (self.server[0], msg["udp_port"])
                            self.server_send("register")
                            self.binding = self.credentials.bind(self.node, self.peer_name, self.sid)
                            await ws.send(json.dumps({"kind": "e2e_identity", "binding": self.binding}))
                        elif msg["kind"] == "peer":
                            pin = self.config.get("peer_fingerprint")
                            if pin is not None and fingerprint(msg["identity"]) != pin:
                                raise ValueError("Peer identity fingerprint changed")
                            verify_binding(msg["binding"], msg["identity"], self.peer_name, self.node)
                            endpoint = tuple(msg["endpoint"])
                            allocation_id, side = msg['allocation_id'], msg['side']
                            if (not isinstance(allocation_id, str) or len(bytes.fromhex(allocation_id)) != 16
                                    or type(side) is not int or side not in (0, 1)):
                                raise ValueError('Invalid relay allocation')
                            if self.allocation_id != allocation_id or self.relay_side != side:
                                self.reset_peer()
                                self.allocation_id, self.relay_side = allocation_id, side
                            elif self.peer_endpoint != endpoint:
                                if self.link:
                                    self.link.path_seen['direct'] = 0
                                    self.link.path_probes.pop('direct', None)
                                    self.link.next_probe['direct'] = 0
                                    self.link.probe_failures['direct'] = 0
                                self.next_hole = self.hole_attempts = 0
                            self.peer_binding, self.peer_identity = msg["binding"], msg["identity"]
                            self.peer_endpoint = endpoint
                            self.metrics["peer_endpoint"] = list(endpoint)
                            self.wake()
                        elif msg["kind"] == "peer_offline":
                            self.reset_peer()
            except Exception as exc:
                if self.stopping:
                    break
                log("control_error", node=self.node, error=type(exc).__name__)
            finally:
                if heartbeat:
                    heartbeat.cancel()
                    await asyncio.gather(heartbeat, return_exceptions=True)
                self.reset_peer()
                self.ws = None
                self.sid = self.key = self.credentials = self.binding = None
                if control_sock is not None:
                    control_sock.close()
            await asyncio.sleep(1)

    async def control_maintenance(self, ws):
        """Control backpressure and renewal must never delay QUIC timers."""
        while self.ws is ws and not self.stopping:
            try:
                await asyncio.wait_for(self.control_wakeup.wait(), 5)
            except TimeoutError:
                pass
            self.control_wakeup.clear()
            renew = (self.stream_count >= MAX_STREAMS_PER_ALLOCATION and not self.streams
                     or self.credentials and time.perf_counter() - self.credentials.created >= 23 * 3600)
            try:
                if self.control_close_requested or renew:
                    await asyncio.wait_for(ws.close(), 3)
                    return
                if self.sid:
                    await asyncio.wait_for(ws.send(json.dumps(dict(kind='heartbeat'))), 3)
            except Exception as exc:
                if not self.stopping:
                    log('control_heartbeat_error', node=self.node, error=type(exc).__name__)
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(ws.close(), 3)
                return

    def maintain(self, now):
        """Synchronous data maintenance: no WebSocket or filesystem awaits."""
        if self.key and now >= self.next_register:
            self.server_send('register')
            stable = self.last_registration_reply and now - self.last_registration_reply < 10
            self.next_register = now + min(5 if stable else 1, max(.1, self.session_ttl / 3))
        if self.link:
            self.link.tick(now)
        if self.peer_binding:
            if self.link and self.link.closed:
                self.reset_peer()
                self.request_control_close()
                return
            if self.node < self.peer_name and self.link is None:
                initial_path = 'direct' if self.mode == 'direct-only' else 'relay'
                self.link = PeerLink(self, initial_path)
            # After peer authentication, encrypted path probes themselves create
            # outbound NAT state. Empty hints are bootstrap-only, not keepalives.
            if (self.mode != 'relay-only' and not (self.link and self.link.authenticated)
                    and now >= self.next_hole):
                self.transport.sendto(direct_packet(self.allocation_id, b''), self.peer_endpoint)
                self.hole_attempts += 1
                interval = .25 if self.hole_attempts < 8 else min(8, 2 ** min(self.hole_attempts - 8, 3))
                self.next_hole = now + interval
            self.select_path()

    def maintenance_deadline(self, now):
        deadline = now + 1  # Idle safety check, rather than a 100 Hz poll.
        if self.key:
            deadline = min(deadline, self.next_register)
        if self.peer_binding and self.mode != 'relay-only' and not (self.link and self.link.authenticated):
            deadline = min(deadline, self.next_hole)
        if self.link and not self.link.closed:
            deadline = min(deadline, self.link.maintenance_deadline(now))
        return deadline

    async def tick(self):
        while True:
            self.maintain(time.perf_counter())
            now = time.perf_counter()
            delay = max(.001, self.maintenance_deadline(now) - now)
            # No await occurred during maintenance. Clear synchronous self-wakes;
            # subsequent socket callbacks / application sends wake this wait.
            self.wakeup.clear()
            try:
                await asyncio.wait_for(self.wakeup.wait(), delay)
            except TimeoutError:
                pass

    async def start(self):
        self.started = time.perf_counter()
        addresses = await asyncio.get_running_loop().getaddrinfo(self.config['server'],
            self.config.get('udp_port', 3478), family=socket.AF_INET, type=socket.SOCK_DGRAM)
        self.server = addresses[0][4]
        sock = bound_ipv4_socket(self.config, socket.SOCK_DGRAM, self.config.get("bind_port", 0))
        try:
            await asyncio.get_running_loop().create_datagram_endpoint(lambda: self, sock=sock)
        except BaseException:
            sock.close()
            raise
        self.tasks = {asyncio.create_task(self.control_loop()), asyncio.create_task(self.tick())}

    async def close(self):
        self.stopping = True
        self.writable.set()
        if self.ws:
            await self.ws.close()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.transport:
            self.transport.close()
