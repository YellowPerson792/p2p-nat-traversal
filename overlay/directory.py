"""Proven device enrollment, online directory and session-scoped mutual consent."""
import asyncio
import contextlib
from dataclasses import dataclass
import secrets
import re
import time

from fastapi import WebSocket, WebSocketDisconnect

from .identity import fingerprint, valid_node, verify, approve_request

MAX_CONNECTIONS = 16
MAX_CONSENTS = 256


@dataclass
class Presence:
    node: str
    fingerprint: str
    ws: WebSocket


@dataclass
class Consent:
    source: Presence
    target: Presence
    port: int
    protocol: str
    id: str = ''

    @property
    def participants(self):
        return {self.source.node: self.source, self.target.node: self.target}


class Directory:
    def __init__(self, coordinator):
        self.coord = coordinator
        self.online = {}
        self.pending = {}
        self.requests = {}
        self.consents = {}
        self.lock = asyncio.Lock()

    def member(self, node, expected=None):
        return self.coord.policy.grant(node, purpose='directory', expected=expected)

    def ticket(self, token, node, peer):
        consent = self.consents.get(token)
        if not consent or set(consent.participants) != {node, peer}:
            raise ValueError('Peer consent required')
        for name, presence in consent.participants.items():
            if self.online.get(name) is not presence:
                raise ValueError('Consent session ended')
            self.member(name, presence.fingerprint)
        return self.member(node, consent.participants[node].fingerprint)

    def permits(self, session):
        try:
            grant = self.ticket(session.consent, session.node, session.peer)
            return fingerprint(grant['public_key']) == session.fingerprint
        except ValueError:
            return False

    async def send(self, presence, message):
        await asyncio.wait_for(presence.ws.send_json(message), 3)

    def connection_count(self, node):
        return (sum(node in pair.participants for pair in self.consents.values())
                + sum(node in (r['source'].node, r['target'].node) for r in self.requests.values()))

    async def broadcast(self):
        for current in list(self.online.values()):
            peers = []
            for other in self.online.values():
                if other is current:
                    continue
                try:
                    self.member(other.node, other.fingerprint)
                except ValueError:
                    continue
                count = self.connection_count(other.node)
                peers.append(dict(node=other.node, fingerprint=other.fingerprint,
                                  connections=count, available=count < MAX_CONNECTIONS))
            with contextlib.suppress(Exception):
                await self.send(current, {'kind': 'directory', 'peers': peers})

    async def cancel_consent(self, token, reason):
        pair = self.consents.pop(token, None)
        if not pair:
            return
        for presence in pair.participants.values():
            session = self.coord.nodes.get((token, presence.node))
            if session and session.consent == token:
                await self.coord.remove(session, reason)
                with contextlib.suppress(Exception):
                    await session.ws.close(code=4000, reason=reason)
            with contextlib.suppress(Exception):
                await self.send(presence, {'kind': 'connection_closed', 'id': pair.id, 'reason': reason})

    async def remove(self, presence):
        if self.online.get(presence.node) is presence:
            self.online.pop(presence.node, None)
        for token, pair in list(self.consents.items()):
            if pair.participants.get(presence.node) is presence:
                await self.cancel_consent(token, 'directory session ended')
        for key, request in list(self.requests.items()):
            if presence in (request['source'], request['target']):
                await self.finish_request(key, False, 'device offline')
        await self.broadcast()

    async def finish_request(self, key, accepted, reason='declined'):
        request = self.requests[key]
        source, target = request['source'], request['target']
        if accepted:
            if request['expires'] < time.monotonic() or any(
                    self.online.get(p.node) is not p for p in (source, target)):
                accepted, reason = False, 'request expired or device offline'
        if accepted and len(self.consents) >= MAX_CONSENTS:
            accepted, reason = False, 'Connection capacity reached'
        if accepted:
            try:
                for presence in (source, target):
                    self.member(presence.node, presence.fingerprint)
            except ValueError:
                accepted, reason = False, 'membership changed'
        self.requests.pop(key)
        if accepted:
            token = secrets.token_hex(32)
            self.consents[token] = Consent(source, target, request['port'], request['protocol'], key)
            try:
                for current, other in ((source, target), (target, source)):
                    await self.send(current, {'kind': 'connection_approved', 'id': key,
                        'peer': other.node, 'fingerprint': other.fingerprint, 'ticket': token,
                        'port': request['port'], 'protocol': request['protocol'],
                        'direction': 'outgoing' if current is source else 'incoming'})
            except Exception:
                await self.cancel_consent(token, 'approval delivery failed')
                raise
        else:
            for presence in (source, target):
                with contextlib.suppress(Exception):
                    await self.send(presence, {'kind': 'request_finished', 'id': key, 'reason': reason})
        await self.broadcast()

    async def command(self, presence, msg):
        async with self.lock:
            if msg.get('kind') == 'heartbeat':
                return
            if msg.get('kind') == 'request_connection':
                port, protocol = msg.get('port'), msg.get('protocol')
                if type(port) is not int or not 1 <= port <= 65535 or protocol not in ('tcp', 'udp'):
                    raise ValueError('A target port in 1..65535 and TCP/UDP protocol are required')
                target = self.online.get(msg.get('peer'))
                if target is None or target is presence:
                    raise ValueError('Target unavailable')
                if len(self.requests) >= 64 or any(self.connection_count(p.node) >= MAX_CONNECTIONS
                                                   for p in (presence, target)):
                    raise ValueError('Device connection limit reached')
                key = msg.get('id')
                if (not isinstance(key, str) or not re.fullmatch(r'[0-9a-f]{32}', key)
                        or key in self.requests or any(pair.id == key for pair in self.consents.values())):
                    raise ValueError('A unique connection ID is required')
                self.requests[key] = dict(source=presence, target=target, port=port,
                                          protocol=protocol, expires=time.monotonic() + 60)
                await self.send(target, {'kind': 'connection_request', 'id': key,
                    'peer': presence.node, 'fingerprint': presence.fingerprint, 'port': port, 'protocol': protocol})
                await self.send(presence, {'kind': 'request_sent', 'id': key, 'peer': target.node,
                                          'port': port, 'protocol': protocol})
            elif msg.get('kind') == 'respond_connection':
                request = self.requests.get(msg.get('id'))
                if request is None or request['target'] is not presence or type(msg.get('accept')) is not bool:
                    raise ValueError('Only the requested device may approve')
                await self.finish_request(msg['id'], msg['accept'])
            elif msg.get('kind') == 'disconnect':
                key = msg.get('id')
                if key is not None and (not isinstance(key, str) or not re.fullmatch(r'[0-9a-f]{32}', key)):
                    raise ValueError('Invalid connection ID')
                matched = False
                for token, pair in list(self.consents.items()):
                    if pair.participants.get(presence.node) is presence and (key is None or pair.id == key):
                        matched = True
                        await self.cancel_consent(token, 'disconnected by peer')
                for request_id, request in list(self.requests.items()):
                    if presence in (request['source'], request['target']) and (key is None or request_id == key):
                        matched = True
                        await self.finish_request(request_id, False, 'cancelled')
                if key is not None and not matched:
                    raise ValueError('Connection not found')
                await self.broadcast()
            else:
                raise ValueError('Unknown directory command')

    async def reap(self):
        async with self.lock:
            for key, request in list(self.requests.items()):
                if request['expires'] < time.monotonic():
                    await self.finish_request(key, False, 'expired')
            for presence in list(self.online.values()):
                try:
                    self.member(presence.node, presence.fingerprint)
                except ValueError:
                    await self.remove(presence)
                    with contextlib.suppress(Exception):
                        await presence.ws.close(code=4003)

    def admin_status(self):
        return dict(pending=[dict(id=key, **entry['request']) for key, entry in self.pending.items()],
                    members={node: dict(fingerprint=fingerprint(member['public_key']),
                        enabled=member['enabled'], online=node in self.online)
                        for node, member in self.coord.policy.load(force=True).items()})

    def decide_enrollment(self, key, accept, expected):
        entry = self.pending.get(key)
        if entry is None or entry['decision'].done():
            raise ValueError('Enrollment request not found')
        if accept:
            approve_request(self.coord.policy.path, entry['request'], expected)
            self.coord.policy.load(force=True)
        if not entry['decision'].done():
            entry['decision'].set_result(accept)

    async def websocket(self, ws):
        await ws.accept()
        presence = None
        pending_key = None
        try:
            hello = await asyncio.wait_for(ws.receive_json(), 5)
            node, public = hello['node'], hello['public_key']
            if not valid_node(node):
                raise ValueError('Invalid node name')
            actual = fingerprint(public)
            if len(self.online) >= 128 or len(self.pending) >= 64:
                raise ValueError('Directory capacity reached')
            challenge = dict(kind='challenge', version=1, purpose='directory', node=node,
                             peer=None, fingerprint=actual, nonce=secrets.token_hex(32))
            await ws.send_json(challenge)
            response = await asyncio.wait_for(ws.receive_json(), 10)
            if response.get('kind') != 'proof':
                raise ValueError('Proof required')
            await asyncio.wait_for(asyncio.to_thread(verify, public, challenge, response['signature']), 6)
            self.coord.policy.load(force=True)
            try:
                self.member(node, actual)
            except ValueError:
                if any(entry['request']['node'] == node for entry in self.pending.values()):
                    raise ValueError('Enrollment already pending for this node')
                pending_key = secrets.token_hex(16)
                decision = asyncio.get_running_loop().create_future()
                self.pending[pending_key] = dict(request=dict(version=1, node=node,
                    public_key=public, fingerprint=actual), decision=decision)
                await ws.send_json({'kind': 'enrollment_pending', 'id': pending_key, 'fingerprint': actual})
                # Poll receive concurrently so disconnected applicants do not leave stale requests.
                incoming = asyncio.create_task(ws.receive_json())
                try:
                    done, _ = await asyncio.wait({decision, incoming}, timeout=300,
                                                return_when=asyncio.FIRST_COMPLETED)
                    if decision not in done or not decision.result():
                        raise ValueError('Enrollment declined, disconnected or expired')
                finally:
                    incoming.cancel()
                    await asyncio.gather(incoming, return_exceptions=True)
                    self.pending.pop(pending_key, None)
                self.member(node, actual)
            async with self.lock:
                if node in self.online:
                    raise ValueError('Device already online')
                presence = Presence(node, actual, ws)
                self.online[node] = presence
                await ws.send_json({'kind': 'directory_welcome', 'node': node, 'fingerprint': actual})
                await self.broadcast()
            while True:
                msg = await asyncio.wait_for(ws.receive_json(), 25)
                self.member(node, actual)
                try:
                    await self.command(presence, msg)
                except (ValueError, KeyError, TypeError) as exc:
                    await self.send(presence, {'kind': 'request_error', 'id': msg.get('id'),
                        'operation': msg.get('kind'), 'error': str(exc)})
        except (WebSocketDisconnect, OSError, TimeoutError, ValueError, KeyError, TypeError):
            with contextlib.suppress(Exception):
                await ws.close(code=4003, reason='Directory admission rejected')
        finally:
            if pending_key:
                self.pending.pop(pending_key, None)
            if presence:
                async with self.lock:
                    await self.remove(presence)
