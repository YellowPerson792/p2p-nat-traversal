"""Shared IPv4 socket binding and Windows UDP robustness options."""
import os
import socket
import struct


def bound_ipv4_socket(config, kind, port=0):
    # AF_INET 限定 IPv4。bind_host 选择本地地址，port=0 让 OS 分配临时端口；
    # 绑定私网地址并不会告诉我们 NAT 对外使用的 IP:port，后者需要服务器观察。
    interface = config.get("interface_index")
    if interface is not None and (os.name != "nt" or not 1 <= interface <= 0xFFFFFF):
        raise ValueError("interface_index requires Windows and an index in 1..16777215")
    sock = socket.socket(socket.AF_INET, kind)
    try:
        if os.name == "nt" and kind == socket.SOCK_DGRAM:
            disable_udp_resets(sock)
        if interface is not None:
            # Winsock 的 IP_UNICAST_IF（31）按网络字节序接收出口接口索引。
            # 在多网卡机器上显式选路，避免控制流量和 UDP 流量走不同的网络出口。
            sock.setsockopt(socket.IPPROTO_IP, 31, struct.pack("!I", interface))
        sock.bind((config.get("bind_host", "0.0.0.0"), port))
        # 非阻塞 socket 交由 asyncio 事件循环等待就绪，避免一次网络等待卡住其他任务。
        sock.setblocking(False)
        return sock
    except BaseException:
        sock.close()
        raise


def disable_udp_resets(sock):
    # Windows 会将 ICMP Port Unreachable 变为 WSAECONNRESET；Python 3.12
    # Proactor 在该错误后可能不再安排 UDP 接收。打洞/地址失效是正常情况，
    # 此 socket 关闭该转换，以 QUIC 的 ACK/超时判断路径，而非信任 ICMP。
    import ctypes
    winsock = ctypes.WinDLL("Ws2_32", use_last_error=True)
    ioctl = winsock.WSAIoctl
    ioctl.argtypes = [ctypes.c_size_t, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong,
        ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p, ctypes.c_void_p]
    ioctl.restype = ctypes.c_int
    enabled, returned = ctypes.c_int(0), ctypes.c_ulong()
    if ioctl(sock.fileno(), 0x9800000C, ctypes.byref(enabled), ctypes.sizeof(enabled),
             None, 0, ctypes.byref(returned), None, None) != 0:
        raise OSError(winsock.WSAGetLastError(), "Cannot disable UDP connection resets")
