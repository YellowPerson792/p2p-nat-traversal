# 服务器承担两件事：通过 TLS/WebSocket 交换身份、密钥和地址；直连失败时转发 UDP。
# 地址发现与 STUN 的作用相似，中继与 TURN 的作用相似，但线上的格式均为自定义协议。
# 阅读顺序：control 建会话 -> datagram_received 注册地址 -> publish 交换候选
#          -> 两客户端自行打洞；需要中继时再走 datagram_received 的 relay 分支。
import asyncio
import contextlib
import secrets
import socket
import time
from dataclasses import dataclass, field

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException

from .identity import Policy, fingerprint, valid_node, verify
from .wire import RELAY_MAGIC, MAX_PACKET, ReplayWindow, parse_packet, read_relay, relay_packet, log, pack, unpack
from .secure import verify_binding
from .network import bound_ipv4_socket


@dataclass
class Session:
    # 一个已授权的控制连接对应一个会话。endpoint 来自 UDP 包的实际源地址，
    # 而不是客户端自报地址；ws 的 TCP 源端口不能代表 UDP 的 NAT 映射。
    node: str
    peer: str
    sid: str
    key: str
    ws: WebSocket
    fingerprint: str = ""
    consent: str | None = None
    last_seen: float = field(default_factory=time.monotonic)
    endpoint: tuple | None = None
    replay: ReplayWindow = field(default_factory=ReplayWindow)
    last_advertised: tuple | None = None
    binding: dict | None = None
    out_seq: int = 0
    allocation_id: str | None = None


@dataclass(frozen=True)
class RelayAllocation:
    id: str
    sessions: tuple[Session, Session]


class Coordinator(asyncio.DatagramProtocol):
    def __init__(self, registry, udp_host, udp_port, ttl=20):
        # 每包调用 stat 在挂载盘上会阻塞事件循环。数据面缓存最多 100ms；
        # 登录/身份绑定等关键控制操作强制刷新，普通 Policy 默认仍立即检查。
        self.policy = Policy(registry, reload_interval=0.1)
        from .directory import Directory
        self.directory = Directory(self)
        self.host, self.port, self.ttl = udp_host, udp_port, ttl
        self.nodes = {}
        self.sessions = {}
        self.allocations = {}
        self.control_lock = asyncio.Lock()
        # nodes 按 (独立授权, 设备名) 找数据会话，避免不同端口实例交叉配对。
        # sessions 用 sid 找 UDP 验签密钥；
        # allocations 按中继分配 ID 绑定两端会话；不保存端到端密钥。
        self.transport = None
        self.instance = secrets.token_hex(16)
        self.tasks = set()
        self.counters = {"relay_packets": 0, "relay_bytes": 0, "rejected_packets": 0,
                         "registrations": 0, "expired_sessions": 0}

    def connection_made(self, transport):
        self.transport = transport

    def schedule(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def datagram_received(self, data, addr):
        try:
            if len(data) > MAX_PACKET:
                raise ValueError("oversize")
            binary = data.startswith(RELAY_MAGIC)
            if binary:
                allocation_id, side, seq, _ = read_relay(data)
                allocation = self.allocations.get(allocation_id)
                if allocation is None:
                    raise ValueError('Unknown relay allocation')
                session, peer = allocation.sessions[side], allocation.sessions[1 - side]
            else:
                session = self.sessions.get(parse_packet(data).get('sid'))
            if (not session or self.sessions.get(session.sid) is not session
                    or time.monotonic() - session.last_seen > self.ttl or not self.authorized(session)):
                raise ValueError('Unknown or unauthorized session')
            if binary:
                _, _, _, payload = read_relay(data, session.key)
            else:
                body = unpack(data, session.key)
                seq = body['seq']
            if not session.replay.accept(seq):
                raise ValueError("replay")
            if not binary:
                if body["kind"] != "register":
                    raise ValueError("UDP registration required")
                changed = session.endpoint != addr
                session.endpoint, session.last_seen = addr, time.monotonic()
                self.transport.sendto(pack({"kind": "registered", "endpoint": list(addr)}, session.key), addr)
                if changed:
                    self.schedule(self.publish(session))
                return
            if (addr != session.endpoint or self.sessions.get(peer.sid) is not peer
                    or self.nodes.get((peer.consent, peer.node)) is not peer
                    or not peer.endpoint or session.allocation_id != allocation_id
                    or peer.allocation_id != allocation_id
                    or time.monotonic() - peer.last_seen > self.ttl or not self.authorized(peer)):
                raise ValueError('Relay source/peer unavailable')
            # Allocation ID resolves the exact admitted sessions, never the
            # latest replacement peer. HMAC authenticates use of that allocation.
            peer.out_seq += 1
            self.transport.sendto(relay_packet(allocation_id, side, peer.key,
                                              peer.out_seq, payload), peer.endpoint)
            self.counters["relay_packets"] += 1
            self.counters["relay_bytes"] += len(data)
        except (ValueError, KeyError, TypeError, AttributeError, UnicodeError):
            self.counters["rejected_packets"] += 1

    async def publish(self, session):
        # 将同一分配 ID 和各端方向编号通知两端，客户端据此绑定本次连接。
        async with self.control_lock:
            await self._publish(session)

    async def _publish(self, session):
        # A 指定 B 且 B 也指定 A，双方都已有 UDP 端点才发布配对。
        # 双方拿到候选后各自主动发 UDP，让各自 NAT 建立对对端的出站/回包状态。
        peer = self.nodes.get((session.consent, session.peer))
        if (self.nodes.get((session.consent, session.node)) is not session or not peer
                or peer.peer != session.node or not session.endpoint or not peer.endpoint):
            return
        if not self.authorized(session) or not self.authorized(peer):
            return
        if not session.binding or not peer.binding:
            return
        allocation = self.allocations.get(session.allocation_id)
        if (allocation is None or peer.allocation_id != allocation.id
                or not any(member is peer for member in allocation.sessions)
                or not any(member is session for member in allocation.sessions)):
            allocation = self.create_allocation(session, peer)
        for current, other in ((session, peer), (peer, session)):
            side = 0 if allocation.sessions[0] is current else 1
            advertised = (allocation.id, side, other.endpoint)
            if current.last_advertised == advertised:
                continue
            with contextlib.suppress(Exception):
                await asyncio.wait_for(current.ws.send_json({"kind": "peer", "node": other.node,
                                            "endpoint": list(other.endpoint),
                                            "binding": other.binding,
                                            "identity": self.grant(other.node, current.node,
                                                ticket=other.consent, expected=other.fingerprint)["public_key"],
                                            "allocation_id": allocation.id, "side": side}), 2)
                current.last_advertised = advertised

    def invalidate_allocation(self, session):
        allocation_id = session.allocation_id
        session.allocation_id = None
        allocation = self.allocations.pop(allocation_id, None)
        if allocation:
            for member in allocation.sessions:
                if member.allocation_id == allocation_id:
                    member.allocation_id = None
                    member.last_advertised = None

    def create_allocation(self, session, peer):
        if (session is peer or not session.consent or session.consent != peer.consent
                or session.peer != peer.node or peer.peer != session.node
                or self.sessions.get(session.sid) is not session
                or self.sessions.get(peer.sid) is not peer):
            raise ValueError('Allocation requires reciprocal live sessions with the same consent')
        self.invalidate_allocation(session)
        self.invalidate_allocation(peer)
        allocation_id = secrets.token_hex(16)
        while allocation_id in self.allocations:
            allocation_id = secrets.token_hex(16)
        allocation = RelayAllocation(allocation_id, tuple(sorted((session, peer), key=lambda member: member.sid)))
        self.allocations[allocation_id] = allocation
        session.allocation_id = peer.allocation_id = allocation_id
        return allocation

    async def remove(self, session, reason):
        self.sessions.pop(session.sid, None)
        if self.nodes.get((session.consent, session.node)) is session:
            self.nodes.pop((session.consent, session.node))
        self.invalidate_allocation(session)
        peer = self.nodes.get((session.consent, session.peer))
        if peer and peer.peer == session.node:
            peer.last_advertised = None
            with contextlib.suppress(Exception):
                await asyncio.wait_for(peer.ws.send_json({"kind": "peer_offline", "node": session.node}), 2)
        log("session_removed", node=session.node, reason=reason)

    async def reap(self):
        # 应用心跳或 UDP 注册会刷新 last_seen。TCP 连接尚在不代表应用仍工作，
        # 所以显式超时回收。ttl 是服务器会话期限，不是路由器 NAT 表项的寿命。
        while True:
            await asyncio.sleep(1)
            await self.directory.reap()
            for session in list(self.sessions.values()):
                if not self.authorized(session):
                    # 审批表热加载：撤销/缩减权限后删除配对，并通知仍获准的对端清理密钥。
                    await self.remove(session, "authorization_revoked")
                    with contextlib.suppress(Exception):
                        await session.ws.close(code=4003, reason="authorization revoked")
                elif time.monotonic() - session.last_seen > self.ttl:
                    self.counters["expired_sessions"] += 1
                    await self.remove(session, "expired")
                    with contextlib.suppress(Exception):
                        await session.ws.close(code=4000, reason="session expired")

    def authorized(self, session):
        return self.directory.permits(session)

    def grant(self, node, peer=None, purpose='connect', expected=None, ticket=None):
        if purpose == 'connect':
            grant = self.directory.ticket(ticket, node, peer)
            if expected is not None and fingerprint(grant['public_key']) != expected:
                raise ValueError('Identity changed')
            return grant
        return self.policy.grant(node, purpose, expected)

    def metrics(self):
        return {"instance": self.instance, **self.counters, "online_nodes": sorted({s.node for s in self.sessions.values()})}


def create_app(registry, udp_host="0.0.0.0", udp_port=3478, ttl=20, admin_token=None):
    coord = Coordinator(registry, udp_host, udp_port, ttl)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        sock = bound_ipv4_socket({"bind_host": udp_host}, socket.SOCK_DGRAM, udp_port)
        try:
            transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(lambda: coord, sock=sock)
        except BaseException:
            sock.close()
            raise
        coord.port = transport.get_extra_info("sockname")[1]
        reap = asyncio.create_task(coord.reap())
        log("server_ready", udp_port=udp_port)
        try:
            yield
        finally:
            reap.cancel()
            await asyncio.gather(reap, return_exceptions=True)
            for task in list(coord.tasks):
                task.cancel()
            await asyncio.gather(*coord.tasks, return_exceptions=True)
            transport.close()

    app = FastAPI(lifespan=lifespan)
    app.state.coordinator = coord
    from .admin_control import install_admin
    install_admin(app, coord.directory, admin_token, registry)

    @app.websocket('/directory')
    async def directory(ws: WebSocket):
        await coord.directory.websocket(ws)

    @app.get("/health")
    async def health():
        return {"status": "ok", "udp_port": coord.port}

    @app.get("/metrics")
    async def metrics():
        # 旧的共享 Bearer token 不再被接受；有 metrics 权限的身份经 /ws 挑战认证读取。
        raise HTTPException(401, "Use an approved metrics identity over /ws")

    @app.websocket("/ws")
    async def control(ws: WebSocket):
        await ws.accept()
        session = None
        try:
            hello = await asyncio.wait_for(ws.receive_json(), 5)
            node, peer = hello["node"], hello.get("peer")
            purpose = hello.get("purpose", "connect")
            if purpose not in ('connect', 'metrics'):
                raise ValueError('Invalid data-control purpose')
            if not valid_node(node) or (purpose == "connect" and (not valid_node(peer) or node == peer)):
                raise ValueError("invalid node or peer")
            coord.policy.load(force=True)
            ticket = hello.get('ticket')
            grant = coord.grant(node, peer, purpose, ticket=ticket)
            key_fingerprint = fingerprint(grant["public_key"])
            challenge = {"kind": "challenge", "version": 1, "purpose": purpose,
                         "node": node, "peer": peer, "fingerprint": key_fingerprint,
                         "nonce": secrets.token_hex(32)}
            await ws.send_json(challenge)
            response = await asyncio.wait_for(ws.receive_json(), 5)
            if response.get("kind") != "proof":
                raise ValueError("identity proof required")
            await asyncio.wait_for(asyncio.to_thread(verify, grant["public_key"], challenge,
                                                     response["signature"]), 6)
            # 验签期间管理员可能撤销权限，必须再次读取授权，不能使用旧快照。
            coord.policy.load(force=True)
            coord.grant(node, peer, purpose, expected=key_fingerprint, ticket=ticket)
            if purpose == "metrics":
                await ws.send_json({"kind": "metrics", "metrics": coord.metrics()})
                await ws.close()
                return
            # 验签 await 期间也可能有另一个连接抢先上线，故在这里检查节点占用。
            if (ticket, node) in coord.nodes:
                await ws.close(code=4009, reason="connection instance already online")
                return
            session = Session(node, peer, secrets.token_hex(16), secrets.token_hex(32), ws,
                              fingerprint=key_fingerprint, consent=ticket)
            # 每次连接发放新的 sid/key；密钥通过 TLS 下发，不能靠未认证 UDP 获取。
            coord.nodes[(ticket, node)] = session
            coord.sessions[session.sid] = session
            coord.counters["registrations"] += 1
            await ws.send_json({"kind": "welcome", "sid": session.sid, "key": session.key,
                                "udp_port": coord.port, "ttl": ttl})
            log("registered", node=node, peer=peer)
            while True:
                msg = await ws.receive_json()
                if msg.get("kind") == "heartbeat":
                    session.last_seen = time.monotonic()
                elif msg.get("kind") == "e2e_identity":
                    if session.binding is not None:
                        raise ValueError("Encryption identity already bound")
                    coord.policy.load(force=True)
                    grant = coord.grant(node, peer, expected=session.fingerprint, ticket=session.consent)
                    await asyncio.to_thread(verify_binding, msg["binding"], grant["public_key"],
                                            node, peer, session.sid)
                    coord.policy.load(force=True)
                    if not coord.authorized(session):
                        raise ValueError("Authorization revoked")
                    session.binding = msg["binding"]
                    await coord.publish(session)
        except (WebSocketDisconnect, asyncio.TimeoutError, ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
            if not isinstance(exc, WebSocketDisconnect):
                log("control_rejected", error=type(exc).__name__, reason=str(exc) if isinstance(exc, ValueError) else "invalid control exchange")
            with contextlib.suppress(Exception):
                await ws.close(code=4003, reason="identity or authorization rejected")
        finally:
            if session and session.sid in coord.sessions:
                await coord.remove(session, "disconnected")

    return app
