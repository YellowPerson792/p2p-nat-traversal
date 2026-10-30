"""Run the project through a USB UDP bridge, preserving remote packet addresses."""
import argparse
import ipaddress
from pathlib import Path
import runpy
import socket
import struct
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def install(gateway):
    import overlay.client as implementation
    import overlay.device as device

    original_client, original_connect = implementation.Client, implementation.connect
    gateway = (str(ipaddress.IPv4Address(gateway)), 49170)

    class RoutedTransport:
        def __init__(self, transport):
            self.transport = transport
            self.headers = {}

        def sendto(self, data, destination):
            if len(data) > 8192:
                raise ValueError('Cellular bridge payload exceeds 8192 bytes')
            header = self.headers.get(destination)
            if header is None:
                if len(self.headers) >= 64:
                    self.headers.clear()
                header = b'OC1' + socket.inet_aton(destination[0]) + struct.pack('!H', destination[1])
                self.headers[destination] = header
            self.transport.sendto(header + data, gateway)

        def __getattr__(self, name):
            return getattr(self.transport, name)

    class CellularClient(original_client):
        def __init__(self, config, *args, **kwargs):
            # The USB gateway determines the local route. Clear old Internet-facing
            # binding overrides so control_loop cannot open a direct server socket.
            config = dict(config, bind_host='0.0.0.0')
            config.pop('interface_index', None)
            super().__init__(config, *args, **kwargs)
            self.metrics['cellular_gateway'] = list(gateway)

        def connection_made(self, transport):
            sock = transport.get_extra_info('socket')
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2 * 1024 * 1024)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 2 * 1024 * 1024)
            super().connection_made(RoutedTransport(transport))

        def datagram_received(self, packet, address):
            if address == gateway and 9 <= len(packet) <= 8201 and packet[:3] == b'OC1':
                source = (socket.inet_ntoa(packet[3:7]), struct.unpack('!H', packet[7:9])[0])
                super().datagram_received(packet[9:], source)
            else:
                self.metrics['unexpected_source_packets'] += 1

    def connect_over_cellular(uri, **options):
        if 'sock' in options:
            raise ValueError('Preconnected control sockets bypass the cellular bridge')
        # Connect via the phone without changing the URI or the verified TLS name.
        return original_connect(uri, host=gateway[0], port=49171, **options)

    implementation.Client = CellularClient
    implementation.connect = connect_over_cellular
    device.connect = connect_over_cellular


def main():
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument('--gateway', required=True, help='Current phone USB IPv4 address')
    parser.add_argument('--file-experiment', action='store_true')
    args, remaining = parser.parse_known_args()
    install(args.gateway)
    sys.argv = [sys.argv[0], *remaining]
    if args.file_experiment:
        runpy.run_path(str(ROOT / 'scripts/diagnostics/file_transfer.py'), run_name='__main__')
    else:
        from overlay.__main__ import main as overlay_main
        overlay_main()


if __name__ == '__main__':
    main()
