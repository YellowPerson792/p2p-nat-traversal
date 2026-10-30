# 节点间 UDP：中继分配 ID + 原样 QUIC 数据报；连接标识由 QUIC 自己管理。
# 注册用 JSON + HMAC；中继用固定二进制头 + 原样 QUIC 数据报 + HMAC。
import hashlib
import hmac
import json
import struct
import time

# 8192 是注册解析上限，不是建议的网络包长。900 是不可靠 Datagram
# 的应用载荷上限；可靠消息可更大，由 QUIC stream 分成 <=1200 字节的
# QUIC 数据报。再加直连/中继外层，不保证对任意路径 MTU 都不分片。
MAX_PACKET = 8192
MAX_PAYLOAD = 900
QUIC_PACKET = 1200
DIRECT_MAGIC, RELAY_MAGIC = b"OD3", b"OR3"
DIRECT_HEADER = struct.Struct("!3s16s")
RELAY_HEADER = struct.Struct("!3s16sBQ")


def direct_packet(allocation_id, data):
    if len(data) > QUIC_PACKET:
        raise ValueError("QUIC packet too large")
    return DIRECT_HEADER.pack(DIRECT_MAGIC, bytes.fromhex(allocation_id)) + data


def read_direct(data):
    if not DIRECT_HEADER.size <= len(data) <= DIRECT_HEADER.size + QUIC_PACKET:
        raise ValueError("invalid direct packet")
    magic, allocation_id = DIRECT_HEADER.unpack_from(data)
    if magic != DIRECT_MAGIC:
        raise ValueError("invalid direct protocol")
    return allocation_id.hex(), data[DIRECT_HEADER.size:]


def relay_packet(allocation_id, side, key, seq, data):
    if side not in (0, 1) or type(side) is not int or not 1 <= len(data) <= QUIC_PACKET:
        raise ValueError("invalid relay side or QUIC payload")
    body = RELAY_HEADER.pack(RELAY_MAGIC, bytes.fromhex(allocation_id), side, seq) + data
    return body + hmac.digest(bytes.fromhex(key), body, "sha256")


def read_relay(data, key=None):
    if not RELAY_HEADER.size + 33 <= len(data) <= RELAY_HEADER.size + QUIC_PACKET + 32:
        raise ValueError("invalid relay packet")
    magic, allocation_id, side, seq = RELAY_HEADER.unpack_from(data)
    if magic != RELAY_MAGIC or side not in (0, 1):
        raise ValueError("invalid relay protocol")
    if key is not None and not hmac.compare_digest(data[-32:], hmac.digest(bytes.fromhex(key), data[:-32], "sha256")):
        raise ValueError("invalid relay HMAC")
    return allocation_id.hex(), side, seq, data[RELAY_HEADER.size:-32]


def log(event, **fields):
    print(json.dumps({"time": time.time(), "event": event, **fields}), flush=True)


def canonical(value):
    # HMAC 对字节计算。即使 JSON 含义相同，键顺序或空格不同也会得到不同摘要，
    # 因而双方必须使用相同的排序和紧凑编码规则。
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def parse_packet(data):
    # 这里只限制长度、解析注册 JSON；身份真实性仍须验证会话 HMAC。
    if len(data) > MAX_PACKET:
        raise ValueError("oversize packet")
    body = json.loads(data)
    if not isinstance(body, dict):
        raise ValueError("packet must be an object")
    return body


def pack(body, key):
    # mac 不参与自身的计算；先对正文签名，再把摘要放进最终报文。
    body = dict(body)
    body["mac"] = hmac.new(bytes.fromhex(key), canonical(body), hashlib.sha256).hexdigest()
    return canonical(body)


def unpack(data, key):
    # 先限制包长，再解析与验签；上层应只在验签成功后更新地址、路径等状态。
    body = parse_packet(data)
    mac = body.pop("mac")
    expected = hmac.new(bytes.fromhex(key), canonical(body), hashlib.sha256).hexdigest()
    # compare_digest 避免普通字符串比较因首个不匹配位置不同而泄漏比较时序。
    if not isinstance(mac, str) or not hmac.compare_digest(mac, expected):
        raise ValueError("invalid signature")
    return body


class ReplayWindow:
    """Bounded sliding window: reject duplicates and packets too old to retain."""

    # UDP 可乱序：已经收到 102 后仍可接受尚未收到的 101，不能只接受更大的序号。
    # seen 记录窗口内已收序号；high 只前进不后退，窗口左侧以外的一律丢弃。
    # 去重不等于可靠传输：这里不会等待缺号、排序交付或请求重传。

    def __init__(self, size=4096):
        self.size = size
        self.high = -1
        self.seen = set()

    def eligible(self, seq):
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
            return False
        if seq <= self.high - self.size or seq in self.seen:
            return False
        return True

    def accept(self, seq):
        if not self.eligible(seq):
            return False
        self.high = max(self.high, seq)
        self.seen.add(seq)
        if len(self.seen) > self.size:
            self.seen = {n for n in self.seen if n > self.high - self.size}
        return True
