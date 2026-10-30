"""Administrator-approved Ed25519 identities; no shared admission secret."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time

from .wire import canonical
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def openssl(*args):
    executable = shutil.which("openssl")
    if executable is None and os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/usr/bin/openssl.exe"
        if candidate.is_file():
            executable = str(candidate)
    if executable is None:
        raise ValueError("OpenSSL with Ed25519 support is required (OpenSSL 3 recommended)")
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    result = subprocess.run([executable, *map(str, args)], stdin=subprocess.DEVNULL,
                            capture_output=True, timeout=5, **options)
    if result.returncode:
        # 不把子进程 stderr 或私钥内容写入共享日志。
        raise ValueError("OpenSSL identity operation failed")
    return result.stdout


def valid_node(value):
    return (isinstance(value, str) and 0 < len(value) <= 64
            and value.replace("-", "").replace("_", "").isascii()
            and value.replace("-", "").replace("_", "").isalnum())


def public_der(private_key):
    data = load_private(private_key).public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    validate_public(data.hex())
    return data


def load_private(path):
    try:
        key = serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
    except (ValueError, TypeError) as exc:
        raise ValueError('Invalid unencrypted Ed25519 private key') from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError('Expected an Ed25519 private key')
    return key


def validate_public(public_key):
    # 只接受 Ed25519 的固定 SubjectPublicKeyInfo 编码，不自行实现椭圆曲线运算。
    if not isinstance(public_key, str) or len(public_key) != 88:
        raise ValueError("Expected an Ed25519 public key")
    data = bytes.fromhex(public_key)
    if len(data) != 44 or data[:12] != bytes.fromhex("302a300506032b6570032100"):
        raise ValueError("Expected an Ed25519 public key")
    return data


def fingerprint(public_key):
    return hashlib.sha256(validate_public(public_key)).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = path.stat() if path.exists() else None
    # 原子替换，让服务器读到完整的上一版或下一版审批表；管理员串行执行写操作。
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, indent=2)
            stream.write("\n")
        if previous is not None and hasattr(os, "chown"):
            os.chown(temporary, previous.st_uid, previous.st_gid)
        temporary.chmod(stat.S_IMODE(previous.st_mode) if previous is not None else 0o600)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def initialize(node, private_key, request_path):
    if not valid_node(node):
        raise ValueError("Invalid node name")
    private_key, request_path = Path(private_key), Path(request_path)
    if private_key.exists() or request_path.exists():
        raise ValueError("Refusing to overwrite an existing identity or request")
    private_key.parent.mkdir(parents=True, exist_ok=True)
    # 私钥在本机产生，只把公开申请文件交给管理员；生成时即使用受限权限。
    generated = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    with private_key.open("xb") as stream:
        private_key.chmod(0o600)
        stream.write(generated)
    public_key = public_der(private_key).hex()
    request = {"version": 1, "node": node, "public_key": public_key,
               "fingerprint": fingerprint(public_key)}
    write_json(request_path, request)
    return request


def auth_message(challenge):
    # 绑定协议用途、节点、目标 peer 和本连接随机挑战；签名不能移到另一用途/连接。
    return b"p2p-overlay-identity-v1\n" + canonical(challenge)


def sign(private_key, challenge):
    return load_private(private_key).sign(auth_message(challenge)).hex()


def verify(public_key, challenge, signature):
    if not isinstance(signature, str) or len(signature) != 128:
        raise ValueError("Invalid identity signature")
    key = serialization.load_der_public_key(validate_public(public_key))
    try:
        key.verify(bytes.fromhex(signature), auth_message(challenge))
    except InvalidSignature as exc:
        raise ValueError('Invalid identity signature') from exc


def proof(private_key, challenge, node, peer, purpose):
    if (challenge.get("kind") != "challenge" or challenge.get("version") != 1
            or challenge.get("node") != node or challenge.get("peer") != peer
            or challenge.get("purpose") != purpose
            or challenge.get("fingerprint") != fingerprint(public_der(private_key).hex())
            or not isinstance(challenge.get("nonce"), str) or len(challenge["nonce"]) != 64):
        raise ValueError("Unexpected identity challenge")
    return {"kind": "proof", "signature": sign(private_key, challenge)}


class Policy:
    def __init__(self, path, reload_interval=0):
        self.path = Path(path)
        self.reload_interval, self.checked = reload_interval, 0
        self.stamp = None
        self.members = {}

    def load(self, force=False):
        now = time.monotonic()
        if not force and now - self.checked < self.reload_interval:
            return self.members
        self.checked = now
        try:
            stat = self.path.stat()
            stamp = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
            if stamp == self.stamp:
                return self.members
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("version") != 1 or not isinstance(data.get("members"), dict):
                raise ValueError("Invalid approval registry")
            members = data["members"]
            for node, member in members.items():
                if not valid_node(node) or not isinstance(member, dict):
                    raise ValueError("Invalid approval entry")
                fingerprint(member["public_key"])
                if type(member.get("enabled")) is not bool or type(member.get("metrics", False)) is not bool:
                    raise ValueError("Invalid approval permissions")
                # Ignore retired static peer lists when reading an older registry.
                members[node] = {key: member[key] for key in ('public_key', 'enabled')}
                members[node]['metrics'] = member.get('metrics', False)
            self.members, self.stamp = members, stamp
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            # 审批文件缺失或损坏时拒绝访问，不继续沿用旧授权。
            self.members, self.stamp = {}, None
        return self.members

    def grant(self, node, purpose="directory", expected=None):
        member = self.load().get(node)
        if not member or not member["enabled"]:
            raise ValueError("Identity not approved")
        if expected is not None and fingerprint(member["public_key"]) != expected:
            raise ValueError("Identity key changed")
        if purpose == "metrics":
            if not member.get("metrics", False):
                raise ValueError("Metrics permission required")
        elif purpose == "directory":
            pass
        else:
            raise ValueError("Invalid identity purpose")
        return member


def _write_registry(path, data):
    for member in data['members'].values():
        member.pop('peers', None)  # migration only; no static peer authorization remains
    write_json(path, data)


def approve(registry, request_path, expected_fingerprint, metrics=False):
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    return approve_request(registry, request, expected_fingerprint, metrics)


def approve_request(registry, request, expected_fingerprint, metrics=False):
    if type(metrics) is not bool:
        raise ValueError('Metrics permission must be a boolean')
    node, public_key = request["node"], request["public_key"]
    actual = fingerprint(public_key)
    public_key = public_key.lower()
    if (request.get("version") != 1 or request.get("fingerprint") != actual
            or actual != expected_fingerprint or not valid_node(node)):
        raise ValueError("Request or independently confirmed fingerprint does not match")
    path = Path(registry)
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"version": 1, "members": {}}
    if data.get("version") != 1 or not isinstance(data.get("members"), dict):
        raise ValueError("Invalid approval registry")
    previous = data["members"].get(node)
    if previous and fingerprint(previous["public_key"]) != actual:
        raise ValueError("Node already bound to another key; use a new node identity")
    if any(n != node and fingerprint(m["public_key"]) == actual for n, m in data["members"].items()):
        raise ValueError("Public key already bound to another node")
    data["members"][node] = {"public_key": public_key, "enabled": True,
                             "metrics": bool(metrics)}
    _write_registry(path, data)
    return {"node": node, "fingerprint": actual, **data["members"][node]}


def revoke(registry, node):
    path = Path(registry)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["members"][node]["enabled"] = False
    _write_registry(path, data)


def fetch_metrics(config):
    # 复用挑战认证，但 purpose=metrics；无需向普通客户端分发组级管理 token。
    import ssl
    from websockets.sync.client import connect
    context = ssl.create_default_context(cafile=config["ca"])
    url = f'wss://{config["server"]}:{config.get("control_port", 8443)}/ws'
    with connect(url, ssl=context, proxy=None, open_timeout=5, max_size=16384) as ws:
        ws.send(json.dumps({"node": config["node"], "peer": None, "purpose": "metrics"}))
        challenge = json.loads(ws.recv(timeout=5))
        ws.send(json.dumps(proof(config["identity"], challenge, config["node"], None, "metrics")))
        result = json.loads(ws.recv(timeout=5))
        if result.get("kind") != "metrics":
            raise ValueError("Expected authorized metrics response")
        return result["metrics"]


def node_config(config, node):
    """A lab controller may hold explicitly named test keys; devices use one key."""
    selected = dict(config)
    if node in config.get("clients", {}):
        selected["identity"] = config["clients"][node]
    elif config.get("node") is not None and config["node"] != node:
        raise ValueError("Config belongs to a different node; provide that node's own config")
    selected["node"] = node
    return selected
