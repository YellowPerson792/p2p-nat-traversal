from types import SimpleNamespace
import asyncio
import socket
import struct

import pytest

import overlay.network as network_module
from overlay.network import bound_ipv4_socket


class FakeSocket:
    def __init__(self):
        self.calls = []
        self.closed = False

    def setsockopt(self, *args):
        self.calls.append(("option", args))

    def bind(self, address):
        self.calls.append(("bind", address))

    def setblocking(self, blocking):
        self.calls.append(("blocking", blocking))

    def close(self):
        self.closed = True


def test_default_binding_has_no_interface_option(monkeypatch):
    sock = FakeSocket()
    monkeypatch.setattr(network_module.socket, "socket", lambda *args: sock)
    monkeypatch.setattr(network_module, "disable_udp_resets", lambda sock: None)
    assert bound_ipv4_socket({}, socket.SOCK_DGRAM, 12345) is sock
    assert sock.calls == [("bind", ("0.0.0.0", 12345)), ("blocking", False)]


@pytest.mark.parametrize("kind", [socket.SOCK_DGRAM, socket.SOCK_STREAM])
def test_windows_interface_uses_network_order_for_udp_and_tcp(monkeypatch, kind):
    sock = FakeSocket()
    monkeypatch.setattr(network_module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(network_module.socket, "socket", lambda *args: sock)
    monkeypatch.setattr(network_module, "disable_udp_resets", lambda sock: None)
    bound_ipv4_socket({"bind_host": "192.0.2.1", "interface_index": 26}, kind)
    assert sock.calls == [("option", (socket.IPPROTO_IP, 31, struct.pack("!I", 26))),
                          ("bind", ("192.0.2.1", 0)), ("blocking", False)]


def test_non_windows_rejects_interface_instead_of_ignoring_it(monkeypatch):
    monkeypatch.setattr(network_module, "os", SimpleNamespace(name="posix"))
    with pytest.raises(ValueError, match="requires Windows"):
        bound_ipv4_socket({"interface_index": 26}, socket.SOCK_DGRAM)


def test_failed_binding_closes_socket(monkeypatch):
    sock = FakeSocket()

    def fail(address):
        raise OSError("invalid local address")

    sock.bind = fail
    monkeypatch.setattr(network_module.socket, "socket", lambda *args: sock)
    with pytest.raises(OSError):
        bound_ipv4_socket({}, socket.SOCK_STREAM)
    assert sock.closed


def test_udp_receive_survives_sending_to_a_closed_port():
    async def run():
        received = asyncio.get_running_loop().create_future()
        class Protocol(asyncio.DatagramProtocol):
            def datagram_received(self, data, addr):
                if not received.done():
                    received.set_result(data)
        sock = bound_ipv4_socket({"bind_host": "127.0.0.1"}, socket.SOCK_DGRAM)
        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(Protocol, sock=sock)
        source = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        closed = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        closed.bind(("127.0.0.1", 0))
        target = closed.getsockname()
        closed.close()
        try:
            transport.sendto(b"probe", target)
            await asyncio.sleep(0.1)
            source.sendto(b"still receiving", sock.getsockname())
            assert await asyncio.wait_for(received, 2) == b"still receiving"
        finally:
            source.close()
            transport.close()
    asyncio.run(run())
