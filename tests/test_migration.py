"""One real encrypted QUIC connection, with a deterministic two-carrier network."""
import asyncio
import time

import pytest

from overlay.secure import MAX_RELIABLE_PAYLOAD, PeerLink
from overlay.streams import MAX_PENDING_BYTES
from overlay.wire import read_relay, relay_packet
from test_identity import identities
from test_secure import pair, pump

FIRST, SECOND = b'a' * 16, b'b' * 16


class Routes:
    def __init__(self, a, b, queue):
        self.queue, self.blocked, self.delayed = queue, set(), []
        self.sequence = 100
        for owner, peer, port in ((a, b, 1001), (b, a, 1002)):
            owner.mode = 'auto'
            owner.key = 'ab' * 32

            class Transport:
                def sendto(_, data, destination, owner=owner, peer=peer, port=port):
                    path = 'relay' if destination == owner.server else 'direct'
                    if path == 'relay':
                        allocation_id, side, _, packet = read_relay(data, owner.key)
                        self.sequence += 1
                        data = relay_packet(allocation_id, side, peer.key, self.sequence, packet)
                        arrival = (peer, data, peer.server)
                    else:
                        arrival = (peer, data, ('127.0.0.1', port))
                    if (owner.node, path) in self.blocked:
                        self.delayed.append(arrival)
                    else:
                        self.queue.append(arrival)
            owner.transport = Transport()


def test_native_quic_rejects_an_unrelated_initial_without_an_outer_cid(identities):
    async def run():
        a, b, queue = pair(identities[1])
        await pump(a, b, queue, lambda: a.ready.is_set() and b.ready.is_set())
        connection = b.link.quic
        # The authorized endpoint and allocation_id match; this Initial belongs to an
        # unrelated QUIC connection. Native destination-CID checks must reject it.
        _, _, other_queue = pair(identities[1])
        _, data, source = other_queue.popleft()
        b.datagram_received(data, source)
        assert b.link.quic is connection and not b.link.closed and b.ready.is_set()
        received = []
        b.on_stream_data = lambda key, data, path: received.append(data)
        assert a.send(b'original stream still works', stream=FIRST)
        await pump(a, b, queue, lambda: bool(received))
        assert received == [b'original stream still works']
    asyncio.run(run())


def test_initial_handshake_works_when_relay_carrier_is_blocked(identities):
    async def run():
        a, b, queue = pair(identities[1], start=False)
        routes = Routes(a, b, queue)
        routes.blocked = {('a', 'relay'), ('b', 'relay')}
        a.link = PeerLink(a, 'relay')
        original = a.link.quic
        await pump(a, b, queue, lambda: a.ready.is_set() and b.ready.is_set()
                   and a.path == b.path == 'direct')
        assert a.link.quic is original and a.link.original_cid == b.link.original_cid
    asyncio.run(run())


def test_native_recovery_and_fin_survive_both_carrier_switches(identities):
    async def run():
        a, b, queue = pair(identities[1])
        routes = Routes(a, b, queue)
        received = []
        b.on_stream_data = lambda key, data, path: received.append((key, data))
        await pump(a, b, queue, lambda: a.path == b.path == 'direct'
                   and all(c.link.path_seen['relay'] for c in (a, b)))
        connection, cid = a.link.quic, a.link.original_cid
        assert cid == connection.original_destination_connection_id
        assert a.send(b'before FIN', stream=FIRST)
        await pump(a, b, queue, lambda: not a.pending_count and bool(received))
        assert a.send(b'keep this stream', stream=SECOND)
        await pump(a, b, queue, lambda: len(received) == 2 and not a.pending_count)
        stream_id = a.link.send_streams[SECOND]

        routes.blocked = {('a', 'direct'), ('b', 'direct')}
        a.close_stream(FIRST)
        b.close_stream(FIRST)  # Lose FINs in both directions.
        assert a.send(b'native retransmission', stream=SECOND)
        await pump(a, b, queue, lambda: len(received) == 3 and FIRST not in a.streams
                   and FIRST not in b.streams and a.path == b.path == 'relay', timeout=8)
        assert a.link.quic is connection and a.link.original_cid == cid
        assert a.link.send_streams[SECOND] == stream_id
        assert routes.delayed
        # Old-path packets may arrive after the new path delivered their bytes.
        queue.extend(routes.delayed)
        routes.delayed.clear()
        await pump(a, b, queue, lambda: not queue and not a.pending_count)
        assert received == [(FIRST, b'before FIN'), (SECOND, b'keep this stream'),
                            (SECOND, b'native retransmission')]
        routes.blocked.clear()
        # A failed backup direct path now backs off instead of probing forever
        # every second; allow its pending-probe expiry and capped retry interval.
        await pump(a, b, queue, lambda: a.path == b.path == 'direct', timeout=12)
        assert a.link.quic is connection and a.link.send_streams[SECOND] == stream_id
        # CID rotation does not create another carrier or affect stream mapping.
        a.link.quic.change_connection_id()
        assert a.send(b'after CID rotation', stream=SECOND)
        await pump(a, b, queue, lambda: len(received) == 4)
        assert received[-1] == (SECOND, b'after CID rotation')
        assert a.link.original_cid == cid
    asyncio.run(run())


def test_asymmetric_carriers_share_control_stream_and_native_state(identities):
    async def run():
        a, b, queue = pair(identities[1])
        Routes(a, b, queue)
        await pump(a, b, queue, lambda: a.ready.is_set() and b.ready.is_set())
        received = []
        a.on_stream_data = lambda key, data, path: received.append(('a', data))
        b.on_stream_data = lambda key, data, path: received.append(('b', data))
        # Send in different physical directions before either peer processes it.
        a.link.path = 'relay'
        a.link.publish_path()
        assert a.send(b'relay outbound', stream=FIRST)
        b.link.path = 'direct'
        b.link.publish_path()
        assert b.send(b'direct outbound', stream=FIRST)
        await pump(a, b, queue, lambda: len(received) == 2 and a.link.peer_path is not None
                   and b.link.peer_path is not None)
        assert sorted(received) == [('a', b'direct outbound'), ('b', b'relay outbound')]
        assert a.link.peer_control_stream in a.link.buffers and b.link.peer_control_stream in b.link.buffers
        assert a.link.original_cid == b.link.original_cid
    asyncio.run(run())


def test_native_buffer_backpressure_is_per_stream_and_reset_wakes_sender(identities):
    async def run():
        a, b, queue = pair(identities[1])
        await pump(a, b, queue, lambda: a.ready.is_set() and b.ready.is_set())
        payload = bytes(MAX_RELIABLE_PAYLOAD)
        while a.send(payload, stream=FIRST):
            pass  # No ACK processing: fill aioquic's own sender buffer.
        assert a.link.pending_bytes(FIRST) <= MAX_PENDING_BYTES
        assert a.send(b'interactive', stream=SECOND)
        blocked = asyncio.create_task(a.send_reliable(payload, stream=FIRST))
        await asyncio.sleep(0)
        assert not blocked.done()
        await pump(a, b, queue, blocked.done)
        await blocked
        while a.send(payload, stream=FIRST):
            pass
        blocked = asyncio.create_task(a.send_reliable(payload, stream=FIRST))
        await asyncio.sleep(0)
        assert not blocked.done()
        a.reset_peer()
        with pytest.raises(ConnectionError):
            await blocked
    asyncio.run(run())


def test_wrong_carrier_probe_reply_cannot_validate_direct_path(identities):
    async def run():
        a, b, queue = pair(identities[1])
        await pump(a, b, queue, lambda: a.ready.is_set() and b.ready.is_set())
        nonce = bytes(range(16))
        a.link.path_seen['relay'] = 0
        a.link.path_probes['direct'] = (nonce, time.perf_counter())
        a.link.receive_path = 'relay'
        a.link.receive_datagram_frame(b'\x02' + nonce)
        assert a.link.path_seen['relay'] == 0
        assert 'direct' in a.link.path_probes
    asyncio.run(run())
