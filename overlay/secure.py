"""End-to-end QUIC/TLS, with signed device bindings and client authentication.

One QUIC connection uses direct / relay carriers. The coordinator sees opaque QUIC.
The carrier adapter deliberately presents a stable peer address to aioquic.
"""
from datetime import datetime, timedelta, timezone
import contextlib
from collections import deque
import hashlib
import json
from pathlib import Path
import secrets
import ssl
import struct
import time

from aioquic.buffer import Buffer
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.connection import QuicConnection
from aioquic.quic.events import (ConnectionTerminated, DatagramFrameReceived,
    HandshakeCompleted, StreamDataReceived, StreamReset, StopSendingReceived)
from aioquic.quic.packet import pull_quic_header, QuicPacketType
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID

from .identity import auth_message, validate_public
from .wire import MAX_PAYLOAD, QUIC_PACKET, canonical
from .streams import MAX_PENDING_BYTES, MAX_TOTAL_BYTES, MAX_STREAMS, MAX_STREAMS_PER_ALLOCATION

# Identity bindings remain compatible, but the stream/recovery wire format is new.
# Fail ALPN negotiation with old peers instead of silently mixing ordering rules.
SUITE = "overlay-quic-v1"
LINK_PROTOCOL = "overlay-quic-v8"
DATA, BIND, RESET, PATH = range(1, 5)
HELLO, CHALLENGE, PROOF, READY = range(5, 9)
AUTH_STREAM = 0
CONTROL_STREAM_CLIENT, CONTROL_STREAM_SERVER = 2, 3
MAX_RELIABLE_PAYLOAD = 16 * 1024


class TransportIdentity:
    def __init__(self, identity):
        self.created = time.perf_counter()
        self.identity = serialization.load_pem_private_key(Path(identity).read_bytes(), password=None)
        if not isinstance(self.identity, Ed25519PrivateKey):
            raise ValueError("Ed25519 identity required")
        self.private = Ed25519PrivateKey.generate()
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "overlay-peer")])
        now = datetime.now(timezone.utc)
        self.certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(self.private.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("overlay-peer")]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(self.private, algorithm=None))
        self.pem = self.certificate.public_bytes(serialization.Encoding.PEM)

    def bind(self, node, peer, sid):
        body = {"kind": "e2e_identity", "suite": SUITE, "node": node, "peer": peer,
                "sid": sid, "certificate": self.pem.decode("ascii")}
        return {"body": body, "signature": self.identity.sign(auth_message(body)).hex()}


def verify_signature(public, body, signature):
    try:
        serialization.load_der_public_key(validate_public(public)).verify(bytes.fromhex(signature), auth_message(body))
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise ValueError("Invalid identity signature") from exc


def verify_binding(binding, identity, node, peer, sid=None):
    body = binding["body"]
    if (body.get("kind") != "e2e_identity" or body.get("suite") != SUITE
            or body.get("node") != node or body.get("peer") != peer
            or not isinstance(body.get("sid"), str) or len(body["sid"]) != 32
            or (sid is not None and body["sid"] != sid)
            or not isinstance(body.get("certificate"), str) or len(body["certificate"]) > 2048):
        raise ValueError("Invalid QUIC identity binding")
    verify_signature(identity, body, binding["signature"])
    certificate = x509.load_pem_x509_certificate(body["certificate"].encode("ascii"))
    if not isinstance(certificate.public_key(), Ed25519PublicKey):
        raise ValueError("Ed25519 certificate required")
    now = datetime.now(timezone.utc)
    if not certificate.not_valid_before_utc <= now <= certificate.not_valid_after_utc:
        raise ValueError('Bound session certificate is not currently valid')
    try:
        usage = certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    except x509.ExtensionNotFound as exc:
        raise ValueError('Bound session certificate has no TLS purpose') from exc
    if ExtendedKeyUsageOID.SERVER_AUTH not in usage:
        raise ValueError('Bound session certificate must allow TLS server authentication')
    return certificate


def is_initial(data):
    return len(data) >= 1200 and pull_quic_header(Buffer(data=data), host_cid_length=8).packet_type == QuicPacketType.INITIAL


class PeerLink:
    def __init__(self, owner, path, initial=None):
        self.owner, self.path = owner, path
        self.receive_path = path
        self.path_seen = {'direct': 0, 'relay': 0}
        self.path_probes = {}
        self.routed_datagrams = deque()
        self.routed_pending = None
        self.next_probe = dict(direct=0, relay=0)
        self.last_probe_sent = dict(direct=0, relay=0)
        self.probe_failures = dict(direct=0, relay=0)
        self.path_announced = None
        self.peer_path = None
        self.peer_mode = None
        self.initiator = owner.node < owner.peer_name
        # Use a monotonic high-resolution clock for every QUIC timestamp.
        # Windows Python 3.12 time.monotonic() advances in 15.625 ms steps,
        # otherwise pacing refills occur in coarse bursts regardless of RTT.
        self.created = time.perf_counter()
        self.last_ack = self.created
        self.updated_key = self.last_activity = self.active_since = self.created
        self.tls_complete = self.authenticated = self.closed = False
        self.control_stream = CONTROL_STREAM_CLIENT if self.initiator else CONTROL_STREAM_SERVER
        self.peer_control_stream = CONTROL_STREAM_SERVER if self.initiator else CONTROL_STREAM_CLIENT
        self.buffers = {AUTH_STREAM: bytearray(), self.peer_control_stream: bytearray()}
        self.buffered_bytes = 0
        self.send_streams, self.receive_streams, self.stream_channels = {}, {}, {}
        self.ended_streams, self.discarded_streams, self.finished_streams = set(), set(), set()
        self.received_fin = set()
        self.hello = secrets.token_hex(32)
        self.challenge = None
        configuration = QuicConfiguration(is_client=self.initiator, alpn_protocols=[LINK_PROTOCOL],
            idle_timeout=10, max_datagram_size=QUIC_PACKET,
            # Keep QUIC flow control above the application window so it does
            # not become the next bottleneck while a file is in flight.
            max_data=MAX_TOTAL_BYTES, max_stream_data=1024 * 1024,
            max_datagram_frame_size=QUIC_PACKET,
            server_name="overlay-peer" if self.initiator else None)
        configuration.certificate = owner.credentials.certificate
        configuration.private_key = owner.credentials.private
        # TLS 验证设备签名绑定的服务端证书；客户端身份在加密的认证流中证明。
        if self.initiator:
            configuration.cadata = owner.peer_binding["body"]["certificate"].encode("ascii")
            configuration.verify_mode = ssl.CERT_REQUIRED
        destination = None
        if initial is not None:
            destination = pull_quic_header(Buffer(data=initial), host_cid_length=8).destination_cid
        verify_binding(owner.peer_binding, owner.peer_identity, owner.peer_name, owner.node)
        self.quic = QuicConnection(configuration=configuration, original_destination_connection_id=destination)
        self.original_cid = self.quic.original_destination_connection_id
        if self.initiator:
            self.quic.connect(("peer", 1), now=self.created)
            self.flush()

    @property
    def available(self):
        now = time.perf_counter()
        return self.authenticated and not self.closed and now - self.last_ack < self.health_timeout(now)

    def active(self, now):
        return now - self.last_activity < 3 or self.pending_count() > 0

    def health_timeout(self, now):
        # On resuming an idle flow, give its immediate probe time to return.
        return 7 if not self.active(now) or now - self.active_since < 3 else 3

    def path_healthy(self, path, now):
        return self.path_seen[path] > 0 and now - self.path_seen[path] < self.health_timeout(now)

    def note_activity(self, now):
        if now - self.last_activity >= 3:
            self.active_since = now
            self.next_probe[self.path] = 0
        self.last_activity = now
        self.owner.wake()

    def probe_paths(self):
        return ('direct',) if self.owner.mode == 'direct-only' else ('relay',) if self.owner.mode == 'relay-only' else ('direct', 'relay')

    def probe_interval(self, path, now):
        if path == self.path:
            return 1 if self.active(now) else 3
        if path == 'direct':
            return min(4, 2 ** min(self.probe_failures[path], 2))
        return 5

    def probe_deadline(self, path, now):
        # A new application burst can shorten a previously idle interval.
        if path == self.path:
            deadline = min(self.next_probe[path], self.last_probe_sent[path] + self.probe_interval(path, now))
        else:
            deadline = self.next_probe[path]
        probe = self.path_probes.get(path)
        return min(deadline, probe[1] + 3) if probe else deadline

    def maintenance_deadline(self, now):
        deadlines = [now + 1]
        timer = self.quic.get_timer()
        if timer is not None:
            deadlines.append(timer)
        if self.authenticated:
            deadlines.extend(self.probe_deadline(path, now) for path in self.probe_paths())
            deadlines.append(self.updated_key + 540)
            for last in (self.last_ack, self.path_seen[self.path]):
                expires = last + self.health_timeout(now)
                if expires > now:
                    deadlines.append(expires)
        return min(deadlines)

    @staticmethod
    def record(kind, payload):
        return struct.pack("!IB", len(payload) + 1, kind) + payload

    def frame(self, kind, payload=b"", channel=None, auth=False):
        if self.closed:
            return
        if channel is None:
            if self.pending_bytes() + len(payload) + 5 > MAX_TOTAL_BYTES + 64 * 1024:
                raise ValueError('QUIC control send buffer full')
            self.quic.send_stream_data(AUTH_STREAM if auth else self.control_stream, self.record(kind, payload))
            return
        stream_id = self.send_streams.get(channel)
        if stream_id is None:
            if len(self.send_streams) >= MAX_STREAMS:
                raise ValueError('QUIC stream limit reached')
            # Each direction owns a uni stream, bound to the same application
            # channel. Native offsets and FINs survive every carrier switch.
            stream_id = self.quic.get_next_available_stream_id(is_unidirectional=True)
            self.send_streams[channel] = stream_id
            self.quic.send_stream_data(stream_id, self.record(BIND, channel))
        if stream_id in self.finished_streams:
            raise ValueError('QUIC stream already finished')
        self.quic.send_stream_data(stream_id, self.record(kind, payload))

    def finish_stream(self, channel):
        stream_id = self.send_streams.get(channel)
        # Opening the reverse direction with BIND + FIN also handles channels
        # which sent no reply bytes. QUIC retransmits FIN across carrier changes.
        if stream_id is None and self.authenticated and not self.closed:
            stream_id = self.quic.get_next_available_stream_id(is_unidirectional=True)
            self.send_streams[channel] = stream_id
            self.quic.send_stream_data(stream_id, self.record(BIND, channel))
        if stream_id is not None and stream_id not in self.finished_streams and not self.closed:
            self.quic.send_stream_data(stream_id, b'', end_stream=True)
            self.finished_streams.add(stream_id)
            self.flush()

    def retire_stream(self, channel, abort=False):
        outgoing = self.send_streams.pop(channel, None)
        incoming = self.receive_streams.pop(channel, None)
        if outgoing is not None:
            if not self.closed:
                if abort:
                    # aioquic may already have collected a FIN/RESET-acked stream.
                    with contextlib.suppress(ValueError):
                        self.quic.reset_stream(outgoing, error_code=0x101)
                elif outgoing not in self.finished_streams:
                    self.quic.send_stream_data(outgoing, b'', end_stream=True)
            self.finished_streams.discard(outgoing)
        if incoming is not None:
            self.discarded_streams.add(incoming)
            self.stream_channels.pop(incoming, None)
            self.buffered_bytes -= len(self.buffers.pop(incoming, b''))
            self.ended_streams.discard(incoming)
            if not self.closed and incoming not in self.received_fin:
                with contextlib.suppress(ValueError):
                    self.quic.stop_stream(incoming, error_code=0x101 if abort else 0)
            self.received_fin.discard(incoming)
        if not self.closed:
            self.flush()

    def consume_stream(self, stream_id):
        buffer = self.buffers[stream_id]
        while len(buffer) >= 4:
            length = struct.unpack_from('!I', buffer)[0]
            if not 1 <= length <= MAX_RELIABLE_PAYLOAD + 1:
                raise ValueError('Invalid frame length')
            if len(buffer) < length + 4:
                break
            frame = bytes(buffer[4:length + 4])
            del buffer[:length + 4]
            self.buffered_bytes -= length + 4
            if stream_id not in (AUTH_STREAM, self.peer_control_stream) and stream_id not in self.stream_channels:
                if frame[0] != BIND or len(frame) != 17:
                    raise ValueError('Stream binding required')
                channel = frame[1:]
                if channel in self.owner.retired_streams:
                    self.buffered_bytes -= len(self.buffers.pop(stream_id, b''))
                    self.discarded_streams.add(stream_id)
                    self.quic.stop_stream(stream_id, error_code=0x101)
                    return
                if channel in self.receive_streams:
                    raise ValueError('Duplicate logical stream binding')
                self.owner.open_stream(channel)
                self.receive_streams[channel] = stream_id
                self.stream_channels[stream_id] = channel
            else:
                self.received_frame(frame[0], frame[1:], self.stream_channels.get(stream_id), control=stream_id)
            if stream_id in self.discarded_streams:
                return
        if stream_id in self.ended_streams:
            if buffer or stream_id in (AUTH_STREAM, self.peer_control_stream) or stream_id not in self.stream_channels:
                raise ValueError('Truncated or unexpected stream end')
            self.ended_streams.discard(stream_id)
            self.received_fin.add(stream_id)
            self.owner.stream_ended(self.stream_channels[stream_id])

    def stream_data(self, event):
        stream_id = event.stream_id
        if stream_id in self.discarded_streams:
            return
        if stream_id not in (AUTH_STREAM, self.peer_control_stream):
            if stream_id & 3 != (3 if self.initiator else 2):
                raise ValueError('Unexpected QUIC stream direction')
        if stream_id not in self.buffers:
            if len(self.buffers) >= MAX_STREAMS + 2 or len(self.discarded_streams) >= MAX_STREAMS_PER_ALLOCATION:
                raise ValueError('QUIC receive stream limit reached')
            self.buffers[stream_id] = bytearray()
        self.buffers[stream_id].extend(event.data)
        self.buffered_bytes += len(event.data)
        limit = 64 * 1024 if stream_id in (AUTH_STREAM, self.peer_control_stream) else MAX_PENDING_BYTES + 16384
        if (len(self.buffers[stream_id]) > limit
                or self.buffered_bytes > MAX_TOTAL_BYTES):
            raise ValueError('QUIC receive buffer full')
        if event.end_stream:
            self.ended_streams.add(stream_id)
        # Only the authentication stream runs before the client proves identity.
        # READY may be overtaken by independent control / data streams.
        if self.tls_complete and (stream_id == AUTH_STREAM or self.authenticated):
            self.consume_stream(stream_id)

    def auth_context(self):
        client, server = sorted((self.owner.node, self.owner.peer_name))
        binding = self.owner.peer_binding if self.initiator else self.owner.binding
        return dict(kind='quic_client_proof', suite=SUITE, node=client, peer=server,
                    allocation_id=self.owner.allocation_id, connection=self.original_cid.hex(),
                    certificate=hashlib.sha256(binding['body']['certificate'].encode('ascii')).hexdigest())

    def received_frame(self, kind, payload, channel=None, control=None):
        if not self.authenticated:
            if control != AUTH_STREAM or channel is not None or not self.tls_complete:
                raise ValueError('Client identity authentication required')
            if kind == HELLO and not self.initiator and self.challenge is None:
                hello = payload.decode('ascii')
                if len(hello) != 64:
                    raise ValueError('Invalid client nonce')
                self.challenge = dict(self.auth_context(), hello=hello, nonce=secrets.token_hex(32))
                self.frame(CHALLENGE, canonical(self.challenge), auth=True)
            elif kind == CHALLENGE and self.initiator and self.challenge is None:
                challenge = json.loads(payload)
                if (not isinstance(challenge, dict)
                        or challenge != dict(self.auth_context(), hello=self.hello, nonce=challenge.get('nonce'))
                        or not isinstance(challenge.get('nonce'), str) or len(challenge['nonce']) != 64):
                    raise ValueError('Wrong client proof context')
                self.challenge = challenge
                self.frame(PROOF, self.owner.credentials.identity.sign(auth_message(challenge)), auth=True)
            elif kind == PROOF and not self.initiator and self.challenge is not None:
                verify_signature(self.owner.peer_identity, self.challenge, payload.hex())
                self.frame(READY, auth=True)
                self.authenticated = True
            elif kind == READY and not payload and self.initiator and self.challenge is not None:
                self.authenticated = True
            else:
                raise ValueError('Client identity authentication required')
            if self.authenticated:
                self.last_ack = time.perf_counter()
                self.path_seen[self.receive_path] = self.last_ack
                self.publish_path()
                self.owner.select_path()
                for stream_id in list(self.buffers):
                    if stream_id != AUTH_STREAM and stream_id not in self.discarded_streams:
                        self.consume_stream(stream_id)
            return
        if control == self.peer_control_stream and kind == PATH:
            if kind != PATH or len(payload) > 256:
                raise ValueError('Unexpected path control frame')
            state = json.loads(payload)
            if (not isinstance(state, dict) or set(state) != {'mode', 'selected'}
                    or state['mode'] not in ('auto', 'direct-only', 'relay-only')
                    or state['selected'] not in ('direct', 'relay')):
                raise ValueError('Invalid peer path state')
            self.peer_path, self.peer_mode = state['selected'], state['mode']
        elif channel is None and control == self.peer_control_stream and kind == RESET and len(payload) == 16:
            self.owner.close_stream(payload, abort=True, notify=False)
        elif channel is not None and kind == DATA and len(payload) <= MAX_RELIABLE_PAYLOAD:
            # No overlay sequence, ACK or replay cache: stream offsets provide
            # ordered, duplicate-free delivery on the *same* QUIC connection.
            self.owner.deliver(payload, self.receive_path, stream=channel)
        else:
            raise ValueError('Unexpected peer frame')

    def publish_path(self):
        if not self.authenticated or self.closed:
            return
        state = (self.owner.mode, self.path)
        if state == self.path_announced:
            return
        self.frame(PATH, canonical(dict(mode=state[0], selected=state[1])))
        self.path_announced = state

    def native_buffers(self):
        # aioquic 1.7.0 has no public send-buffer occupancy / drain API.
        # Isolate this version-pinned, read-only dependency here. We never mutate
        # private QUIC state or keep a second copy of application payloads.
        return self.quic._streams

    def datagrams_pending(self):
        # Same pinned adapter boundary: aioquic exposes no queue occupancy API.
        return len(self.quic._datagrams_pending)

    def pending_bytes(self, channel=None):
        streams = self.native_buffers()
        if channel is not None:
            stream = streams.get(self.send_streams.get(channel))
            return len(stream.sender._buffer) if stream else 0
        return sum(len(stream.sender._buffer) for stream in streams.values())

    def pending_count(self):
        return sum(bool(stream.sender._buffer) for sid, stream in self.native_buffers().items()
                   if sid not in (AUTH_STREAM, self.control_stream))

    def receive_datagram_frame(self, data):
        if not self.authenticated:
            # Unreliable traffic may overtake READY; discard it until client authentication.
            return
        if not data:
            raise ValueError('Empty datagram')
        kind, payload = data[0], data[1:]
        if kind == 0 and len(payload) <= MAX_PAYLOAD:
            self.owner.deliver(payload, self.receive_path, reliable=False)
        elif kind == 1 and len(payload) == 16:
            # Probe reply must use the physical arrival path, independent of the
            # preferred bulk-data path. A reply on another carrier proves nothing.
            self.route_datagram(self.receive_path, b'\x02' + payload)
        elif kind == 2 and len(payload) == 16:
            probe = self.path_probes.get(self.receive_path)
            now = time.perf_counter()
            if probe and probe[0] == payload and now - probe[1] < 3:
                self.path_seen[self.receive_path] = self.last_ack = now
                self.path_probes.pop(self.receive_path, None)
                self.probe_failures[self.receive_path] = 0
                self.owner.metrics['probe_responses'] += 1
        else:
            raise ValueError('Unexpected datagram')

    def receive(self, packet, path=None):
        if path is not None:
            self.receive_path = path
        try:
            if packet:
                self.quic.receive_datagram(packet, ("peer", 1), now=time.perf_counter())
            while (event := self.quic.next_event()) is not None:
                if isinstance(event, HandshakeCompleted):
                    if event.alpn_protocol != LINK_PROTOCOL:
                        raise ValueError("Wrong ALPN")
                    if event.session_resumed or event.early_data_accepted:
                        raise ValueError('TLS resumption / early data is not enabled')
                    self.tls_complete = True
                    # Reserve the authentication stream before any higher bidi
                    # stream could implicitly skip it. Control / data use uni IDs.
                    if self.initiator:
                        self.frame(HELLO, self.hello.encode('ascii'), auth=True)
                    self.quic.send_stream_data(self.control_stream, b'')
                    if self.buffers[AUTH_STREAM]:
                        self.consume_stream(AUTH_STREAM)
                elif isinstance(event, StreamDataReceived):
                    self.stream_data(event)
                elif isinstance(event, (StreamReset, StopSendingReceived)):
                    if event.stream_id in (AUTH_STREAM, self.control_stream, self.peer_control_stream):
                        raise ValueError('Control stream reset')
                    channel = self.stream_channels.get(event.stream_id)
                    if channel is None:
                        channel = next((key for key, sid in self.send_streams.items()
                                        if sid == event.stream_id), None)
                    if isinstance(event, StreamReset):
                        # RESET can arrive before the complete BIND record.
                        # Release that partial parser even when its logical ID
                        # is still unknown; the control RESET tombstones the ID.
                        self.buffered_bytes -= len(self.buffers.pop(event.stream_id, b''))
                        self.ended_streams.discard(event.stream_id)
                        self.received_fin.discard(event.stream_id)
                        self.discarded_streams.add(event.stream_id)
                    if channel is not None:
                        self.owner.close_stream(channel, abort=True, notify=False)
                elif isinstance(event, DatagramFrameReceived):
                    self.receive_datagram_frame(event.data)
                elif isinstance(event, ConnectionTerminated):
                    self.closed = True
            self.owner.writable.set()
            self.flush()
        except (ValueError, KeyError, TypeError, UnicodeError, ConnectionError) as exc:
            self.close(str(exc))
            self.owner.metrics["invalid_packets"] += 1

    def route_datagram(self, path, data):
        # Probes are best effort and bounded. Queue only one routed DATAGRAM in
        # QUIC at a time, retaining its route if pacing postpones transmission.
        if len(self.routed_datagrams) < 8:
            self.routed_datagrams.append((path, data))
        self.flush()

    def flush(self):
        if self.routed_pending is None and self.routed_datagrams and not self.datagrams_pending():
            self.routed_pending, data = self.routed_datagrams.popleft()
            self.quic.send_datagram_frame(data)
        for packet, _ in self.quic.datagrams_to_send(now=time.perf_counter()):
            path = self.routed_pending or self.path
            routed = self.routed_pending is not None
            self.routed_pending = None
            # Only the probe packet uses its candidate carrier. The remainder
            # of a bulk-data batch uses the selected carrier.
            self.owner.emit(path, packet)
            # Bootstrap the *same* handshake over both candidate carriers. A
            # blocked relay must not prevent establishing the direct route.
            # Stop duplicating once authenticated path control has arrived.
            if self.owner.mode == 'auto' and self.peer_path is None and not routed:
                other = 'relay' if path == 'direct' else 'direct'
                self.owner.emit(other, packet)
        self.owner.wake()

    def tick(self, now):
        if self.closed:
            return
        timer = self.quic.get_timer()
        if timer is not None and timer <= now:
            self.quic.handle_timer(now=now)
            self.receive(b'')
            if self.closed:
                return
        if self.authenticated:
            for path in self.probe_paths():
                if now < self.probe_deadline(path, now):
                    continue
                probe = self.path_probes.get(path)
                if probe and now - probe[1] >= 3:
                    self.path_probes.pop(path)
                    self.probe_failures[path] += 1
                    if path != self.path and path == 'direct':
                        self.next_probe[path] = now + self.probe_interval(path, now)
                        continue
                    probe = None
                nonce = probe[0] if probe else secrets.token_bytes(16)
                if probe is None:
                    self.path_probes[path] = (nonce, now)
                self.route_datagram(path, b'\x01' + nonce)
                self.last_probe_sent[path] = now
                self.next_probe[path] = now + self.probe_interval(path, now)
                self.owner.metrics['probes_sent'] += 1
        self.publish_path()
        if self.authenticated and now - self.updated_key >= 540:
            self.quic.request_key_update()
            self.updated_key = now
        self.flush()

    def close(self, reason="link replaced"):
        if not self.closed:
            self.quic.close(error_code=0x100, reason_phrase=reason[:120])
            self.flush()
            self.closed = True
