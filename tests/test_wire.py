import json

import pytest

from overlay.wire import (ReplayWindow, pack, unpack, direct_packet, read_direct,
                          relay_packet, read_relay, QUIC_PACKET)


def test_compact_envelopes_preserve_opaque_quic_and_authenticate_routing():
    allocation_id, key = 'ee' * 16, 'ab' * 32
    payload = bytes(range(256)) * 4 + bytes(QUIC_PACKET - 1024)
    direct = direct_packet(allocation_id, payload)
    relay = relay_packet(allocation_id, 0, key, 7, payload)
    assert len(direct) == 1219 and read_direct(direct) == (allocation_id, payload)
    assert len(relay) == 1260 and read_relay(relay, key) == (allocation_id, 0, 7, payload)
    assert read_direct(direct_packet(allocation_id, b'')) == (allocation_id, b'')
    for offset in (3, 19, 20, 28, len(relay) - 1):
        damaged = bytearray(relay)
        damaged[offset] ^= 1
        with pytest.raises(ValueError):
            read_relay(damaged, key)
    for size in (0, 1, 18):
        with pytest.raises(ValueError):
            read_direct(direct[:size])
    with pytest.raises(ValueError):
        direct_packet(allocation_id, bytes(QUIC_PACKET + 1))
    with pytest.raises(ValueError):
        relay_packet(allocation_id, 0, key, 8, bytes(QUIC_PACKET + 1))
    with pytest.raises(ValueError):
        relay_packet(allocation_id, 0, key, 8, b'')
    for side in (-1, 2, True):
        with pytest.raises(ValueError):
            relay_packet(allocation_id, side, key, 8, b'x')


def test_old_redundant_envelopes_are_rejected():
    allocation_id, key = 'ee' * 16, 'ab' * 32
    direct = direct_packet(allocation_id, b'opaque')
    relay = relay_packet(allocation_id, 0, key, 1, b'opaque')
    with pytest.raises(ValueError):
        read_direct(b'OD2' + direct[3:])
    with pytest.raises(ValueError):
        read_relay(b'OR2' + relay[3:], key)


def test_authenticated_packet_and_tampering():
    key = "ab" * 32
    body = {"kind": "data", "seq": 10}
    packet = pack(body, key)
    assert unpack(packet, key) == body
    changed = json.loads(packet)
    changed["seq"] = 11
    with pytest.raises(ValueError):
        unpack(json.dumps(changed).encode(), key)
    with pytest.raises(ValueError):
        unpack(packet, "cd" * 32)


def test_replay_window_reordering_and_old_packets():
    window = ReplayWindow(size=4)
    assert window.accept(3)
    assert window.accept(1)
    assert not window.accept(3)
    assert window.accept(10)
    assert not window.accept(1)
    assert not window.accept(-1)
    assert not window.accept(True)
    assert window.accept(9)
