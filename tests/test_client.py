from overlay.client import Client
from overlay.wire import pack, direct_packet, relay_packet


class Transport:
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append((data, addr))


def build():
    delivered = []
    client = Client({"server": "127.0.0.1"}, "a", "b",
        on_data=lambda data, path: delivered.append((data, path)))
    client.transport = Transport()
    client.key, client.sid, client.allocation_id = "ab" * 32, "aa" * 16, "bb" * 16
    client.relay_side = 0
    client.peer_endpoint = ("127.0.0.1", 1234)
    return client, delivered


def test_ready_requires_authenticated_connection():
    client, _ = build()
    client.select_path()
    assert not client.ready.is_set()
    assert not client.send(b'no plaintext fallback')


def test_server_reply_requires_hmac_and_records_mapping():
    client, _ = build()
    endpoint = ["203.0.113.10", 45678]
    client.datagram_received(pack({"kind": "registered", "endpoint": endpoint}, "ef" * 32), client.server)
    assert client.metrics["observed_endpoint"] is None
    client.datagram_received(pack({"kind": "registered", "endpoint": endpoint}, client.key), client.server)
    assert client.metrics["observed_endpoints"] == [endpoint]


def test_peer_fault_injection_preserves_server_registration():
    client, _ = build()
    client.drop_peer_udp = True
    client.datagram_received(direct_packet(client.allocation_id, b"probe"), client.peer_endpoint)
    assert client.metrics["peer_packets_dropped"] == 1
    client.datagram_received(pack({"kind": "registered", "endpoint": ["127.0.0.1", 1234]}, client.key), client.server)
    assert client.metrics["observed_endpoint"] == ["127.0.0.1", 1234]


def test_unknown_source_never_creates_connection():
    client, delivered = build()
    client.datagram_received(b"arbitrary packet", ("192.0.2.1", 9999))
    assert not client.link and not delivered
    assert client.metrics["unexpected_source_packets"] == 1
    client.error_received(ConnectionRefusedError("test"))
    assert client.metrics["udp_errors"] == 1


def test_stale_allocation_and_wrong_side_do_not_poison_the_receive_window():
    client, _ = build()
    client.datagram_received(relay_packet('cc' * 16, 1, client.key, 100000, b'old'), client.server)
    client.datagram_received(relay_packet(client.allocation_id, 0, client.key, 100001, b'reflected'), client.server)
    assert client.server_replay.high == -1 and client.link is None
    assert client.metrics['invalid_packets'] == 2
