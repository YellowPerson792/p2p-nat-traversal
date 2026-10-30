"""The optional USB adapter must preserve identities and reject untrusted envelopes."""
import importlib.util
from pathlib import Path
import struct
import sys

import overlay.client as implementation
import overlay.device as device


def load_adapter():
    source = Path(__file__).resolve().parents[1] / 'scripts/android-cellular/overlay_via_cellular.py'
    spec = importlib.util.spec_from_file_location('cellular_adapter', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_control_keeps_tls_identity_and_clears_old_route(monkeypatch):
    calls = []
    original = implementation.Client
    monkeypatch.setattr(implementation, 'Client', original)
    monkeypatch.setattr(implementation, 'connect', lambda uri, **kw: calls.append((uri, kw)))
    monkeypatch.setattr(device, 'connect', device.connect)
    load_adapter().install('10.0.0.1')
    client = implementation.Client({'server': '192.0.2.1', 'bind_host': '192.0.2.10',
        'interface_index': 7}, 'a', 'b')
    assert client.config['bind_host'] == '0.0.0.0'
    assert 'interface_index' not in client.config
    tls = object()
    implementation.connect('wss://192.0.2.1:8443/ws', ssl=tls, proxy=None)
    assert calls == [('wss://192.0.2.1:8443/ws',
        {'ssl': tls, 'proxy': None, 'host': '10.0.0.1', 'port': 49171})]
    device.connect('wss://192.0.2.1:8443/directory', ssl=tls, proxy=None)
    assert calls[-1] == ('wss://192.0.2.1:8443/directory',
        {'ssl': tls, 'proxy': None, 'host': '10.0.0.1', 'port': 49171})


def test_only_gateway_can_supply_original_udp_address(monkeypatch):
    received = []
    monkeypatch.setattr(implementation, 'Client', implementation.Client)
    monkeypatch.setattr(implementation, 'connect', implementation.connect)
    monkeypatch.setattr(device, 'connect', device.connect)
    monkeypatch.setattr(implementation.Client, 'datagram_received',
        lambda self, data, source: received.append((data, source)))
    load_adapter().install('10.0.0.1')
    client = implementation.Client({'server': '192.0.2.1'}, 'a', 'b')
    packet = b'OC1\xc0\x00\x02\x01' + struct.pack('!H', 3478) + b'QUIC payload'
    client.datagram_received(packet, ('10.0.0.2', 49170))
    client.datagram_received(packet, ('10.0.0.1', 49171))
    client.datagram_received(b'OC1', ('10.0.0.1', 49170))
    assert received == []
    assert client.metrics['unexpected_source_packets'] == 3
    client.datagram_received(packet, ('10.0.0.1', 49170))
    assert received == [(b'QUIC payload', ('192.0.2.1', 3478))]


def test_file_flag_is_not_abbreviated_as_file_experiment(monkeypatch):
    adapter = load_adapter()
    monkeypatch.setattr(adapter, 'install', lambda gateway: None)
    monkeypatch.setattr(sys, 'argv', ['adapter', '--gateway', '10.0.0.1',
        '--file-experiment', 'send', '--file', 'video.mp4'])
    observed = []
    monkeypatch.setattr(adapter.runpy, 'run_path', lambda *a, **kw: observed.append(sys.argv[1:]))
    adapter.main()
    assert observed == [['send', '--file', 'video.mp4']]
