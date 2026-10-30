"""OpenSSH ProxyCommand helper: TCP over SOCKS5, with remote DNS resolution."""
import argparse
import socket
import struct
import sys
import threading


def receive(sock, count):
    data = bytearray()
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise ConnectionError("SOCKS response ended early")
        data.extend(chunk)
    return bytes(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host")
    parser.add_argument("port", type=int)
    parser.add_argument("--proxy-host", default="127.0.0.1")
    parser.add_argument("--proxy-port", type=int, default=1080)
    args = parser.parse_args()
    with socket.create_connection((args.proxy_host, args.proxy_port), timeout=15) as sock:
        sock.sendall(b"\x05\x01\x00")
        if receive(sock, 2) != b"\x05\x00":
            raise ConnectionError("SOCKS authentication negotiation failed")
        host = args.host.encode("idna")
        if not 0 < len(host) <= 255:
            raise ValueError("Destination hostname is too long")
        sock.sendall(b"\x05\x01\x00\x03" + bytes([len(host)]) + host + struct.pack("!H", args.port))
        version, status, reserved, kind = receive(sock, 4)
        if version != 5 or reserved or status:
            raise ConnectionError(f"SOCKS CONNECT failed: {status}")
        length = {1: 4, 4: 16}.get(kind)
        if kind == 3:
            length = receive(sock, 1)[0]
        if length is None:
            raise ConnectionError("Invalid SOCKS address type")
        receive(sock, length + 2)
        sock.settimeout(None)

        def upload():
            try:
                while data := sys.stdin.buffer.read1(65536):
                    sock.sendall(data)
                sock.shutdown(socket.SHUT_WR)
            except OSError:
                sock.close()

        threading.Thread(target=upload, daemon=True).start()
        while data := sock.recv(65536):
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
