import asyncio

import pytest

from overlay.streams import MAX_STREAMS, MAX_STREAMS_PER_ALLOCATION
from overlay.secure import MAX_RELIABLE_PAYLOAD
from test_client import build
from test_identity import identities
from test_secure import pair, pump


FIRST, SECOND = b'a' * 16, b'b' * 16


def test_stream_limits_and_retired_ids_bound_memory():
    client, _ = build()
    for number in range(MAX_STREAMS):
        client.open_stream(number.to_bytes(16, 'big'))
    with pytest.raises(ConnectionError):
        client.open_stream(b'x' * 16)
    client.close_stream(bytes(16), abort=True)
    client.open_stream(b'x' * 16)
    assert len(client.streams) == MAX_STREAMS
    client.stream_count = MAX_STREAMS_PER_ALLOCATION
    with pytest.raises(ConnectionError):
        client.open_stream(b'y' * 16)


def test_lost_quic_stream_packet_does_not_hold_back_other_stream(identities):
    async def run():
        a, b, queue = pair(identities[1])
        received = []
        b.on_stream_data = lambda key, data, path: received.append((key, data))
        await pump(a, b, queue, lambda: a.ready.is_set() and b.ready.is_set())
        queue.clear()
        assert a.send(b'lost first stream packet', stream=FIRST)
        assert queue
        queue.clear()  # Drop this stream's encrypted packet on the wire.
        assert a.send(b'other stream progresses', stream=SECOND)
        await pump(a, b, queue, lambda: bool(received))
        assert received[0] == (SECOND, b'other stream progresses')
        link = a.link
        assert link.send_streams[FIRST] != link.send_streams[SECOND]
        await pump(a, b, queue, lambda: len(received) == 2 and not a.pending_count)
        assert received.count((FIRST, b'lost first stream packet')) == 1
    asyncio.run(run())


def test_graceful_fin_and_abort_release_only_their_stream(identities):
    async def run():
        a, b, queue = pair(identities[1])
        received = []
        b.on_stream_data = lambda key, data, path: received.append((key, data))
        await pump(a, b, queue, lambda: a.ready.is_set() and b.ready.is_set())
        assert a.send(b'complete before FIN', stream=FIRST)
        a.close_stream(FIRST)
        await pump(a, b, queue, lambda: bool(received) and not a.pending_count)
        b.close_stream(FIRST)
        await pump(a, b, queue, lambda: FIRST not in a.streams and FIRST not in b.streams)
        assert not a.link.send_streams and not b.link.receive_streams
        assert a.send(b'abort this', stream=SECOND)
        a.close_stream(SECOND, abort=True)
        await pump(a, b, queue, lambda: SECOND in b.retired_streams)
        assert SECOND not in b.streams
        await pump(a, b, queue, lambda: not a.pending_bytes)
        assert not b.link.buffered_bytes
    asyncio.run(run())


def test_sequential_streams_release_quic_slots_and_application_state(identities):
    async def run():
        a, b, queue = pair(identities[1])
        await pump(a, b, queue, lambda: a.ready.is_set() and b.ready.is_set())
        for number in range(80):  # More than the concurrent proxy/transport limit.
            key = number.to_bytes(16, 'big')
            assert a.send(b'complete', stream=key)
            await pump(a, b, queue, lambda: not a.pending_count)
            a.close_stream(key)
            b.close_stream(key)
            await pump(a, b, queue, lambda: not a.streams and not b.streams)
        await pump(a, b, queue, lambda: not a.pending_bytes and not b.pending_bytes)
        for client in (a, b):
            link = client.link
            assert not link.send_streams and not link.receive_streams
            assert not link.stream_channels and len(link.buffers) == 2
            assert not client.pending_bytes and not client.link.buffered_bytes
        assert len(a.retired_streams) == len(b.retired_streams) == 80
    asyncio.run(run())


def test_abort_reclaims_a_partially_received_stream_binding(identities):
    async def run():
        from overlay.secure import BIND
        a, b, queue = pair(identities[1])
        await pump(a, b, queue, lambda: a.ready.is_set() and b.ready.is_set())
        a.open_stream(FIRST)
        outgoing, incoming = a.link, b.link
        stream_id = outgoing.quic.get_next_available_stream_id(is_unidirectional=True)
        outgoing.send_streams[FIRST] = stream_id
        outgoing.quic.send_stream_data(stream_id, outgoing.record(BIND, FIRST)[:10])
        outgoing.flush()
        await pump(a, b, queue, lambda: len(incoming.buffers.get(stream_id, b'')) == 10)
        a.close_stream(FIRST, abort=True)
        await pump(a, b, queue, lambda: stream_id not in incoming.buffers and FIRST in b.retired_streams)
        assert incoming.buffered_bytes == 0
        received = []
        b.on_stream_data = lambda key, data, path: received.append((key, data))
        assert a.send(b'still works', stream=SECOND)
        await pump(a, b, queue, lambda: bool(received))
        assert received == [(SECOND, b'still works')]
    asyncio.run(run())


def test_data_stream_waits_for_delayed_client_authentication_ready(identities):
    async def run():
        a, b, queue = pair(identities[1])
        received = []
        a.on_stream_data = lambda key, data, path: received.append((key, data))
        original = b.transport.sendto
        dropped = False
        def send(data, destination):
            nonlocal dropped
            if not dropped and b.link and b.link.authenticated and not a.ready.is_set():
                dropped = True  # Lose authentication READY; independent data can overtake it.
                return
            original(data, destination)
        b.transport.sendto = send
        await pump(a, b, queue, lambda: b.ready.is_set())
        assert dropped and not a.ready.is_set()
        b.link.quic._loss._pacer.packet_time = None  # Force wire reordering deterministically.
        assert b.send(b'early independent stream', stream=FIRST)
        queued = list(queue)
        queue.clear()
        for owner, data, source in queued:
            owner.datagram_received(data, source)
        assert not received and not a.link.authenticated
        assert a.link.buffered_bytes > 0
        await pump(a, b, queue, lambda: bool(received))
        assert a.ready.is_set() and received == [(FIRST, b'early independent stream')]
    asyncio.run(run())


def test_previous_session_envelope_alpn_is_rejected(identities, monkeypatch):
    async def run():
        import overlay.secure as secure
        with monkeypatch.context() as patch:
            patch.setattr(secure, 'LINK_PROTOCOL', 'overlay-quic-v7')
            a, b, queue = pair(identities[1])
        await pump(a, b, queue, lambda: any(link.closed for client in (a, b)
                                           for link in ([client.link] if client.link else [])))
        assert not a.ready.is_set() and not b.ready.is_set()
    asyncio.run(run())
