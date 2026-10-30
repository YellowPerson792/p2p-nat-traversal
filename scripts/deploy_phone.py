"""Deploy a client zipapp and isolated crypto runtime over existing USB SSH."""
import argparse
import hashlib
import importlib.metadata
import json
import shlex
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build_client(destination):
    distribution = importlib.metadata.distribution("websockets")
    if distribution.version != "16.1.1":
        raise RuntimeError("Build with websockets==16.1.1 from requirements.lock")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("__main__.py", "from overlay.__main__ import main\nmain()\n")
        for name in ("__init__.py", "__main__.py", "client.py", "wire.py", "identity.py", "secure.py", "network.py", "runtime.py", "proxy.py", "control.py", "device.py", "management.py", "cli.py", "webui.py", "connection.py", "streams.py", "config.py", "doctor.py"):
            archive.write(ROOT / "overlay" / name, "overlay/" + name)
        for source in (ROOT / 'overlay/web/client').iterdir():
            if source.suffix in {'.html', '.js', '.css'}:
                archive.write(source, source.relative_to(ROOT).as_posix())
        for item in distribution.files or ():
            name = item.as_posix()
            if (name.startswith("websockets/") and name.endswith(".py")) or (
                ".dist-info/" in name and (
                    "/licenses/" in name or name.endswith(("/METADATA", "/WHEEL"))
                )
            ):
                archive.write(distribution.locate_file(item), name)
    return hashlib.sha256(destination.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=2222)
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument("--node", default="phone", help="Administrator-approved phone node name")
    args = parser.parse_args()
    folder = ROOT / ".local" / "phone"
    app = folder / "client.pyz"
    digest = build_client(app)
    subprocess.run([sys.executable, str(app), "--help"],
                   check=True, stdout=subprocess.DEVNULL)
    print(f"Client archive: {app.stat().st_size} bytes; sha256={digest}")
    if args.build_only:
        return
    options = ["-i", str(folder / "id_ed25519_usb"), "-o", "IdentitiesOnly=yes",
               "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
               "-o", "HostKeyAlias=ep2120-pixel10-debian-usb", "-o", "ConnectTimeout=5"]
    target = "droid@127.0.0.1"
    ssh = ["ssh", *options, "-p", str(args.port), target]

    remote_interpreter = "python3"

    def remote_python(code):
        return subprocess.run([*ssh, shlex.quote(remote_interpreter) + " -"], input=code, text=True,
                              capture_output=True, check=True).stdout.strip()

    home = remote_python("from pathlib import Path; print(Path.home())")
    remote = home + "/p2p-overlay-client"
    existing_node = remote_python(f"import json\nfrom pathlib import Path\np=Path({remote!r})/'identity.request.json'\nprint(json.loads(p.read_text())['node'] if p.exists() else '')")
    if existing_node and existing_node != args.node:
        raise RuntimeError("Phone identity belongs to another node; refusing to overwrite its configuration")
    config = json.loads((ROOT / ".local/remote/server.json").read_text())
    # Strip host-specific routing and credentials unrelated to this deployment.
    config = {key: config[key] for key in ("server", "control_port", "udp_port") if key in config}
    config["ca"] = remote + "/server.crt"
    config["node"] = args.node
    config["identity"] = remote + "/identity.key"
    config_path = folder / "client.json"
    config_path.write_text(json.dumps(config, indent=2))
    remote_python(f"import os\nfrom pathlib import Path\np=Path({remote!r})\np.mkdir(mode=0o700, exist_ok=True)\nos.chmod(p, 0o700)")
    for source, name in ((app, "client.pyz"), (config_path, "client.json"),
                         (ROOT / ".local/remote/server.crt", "server.crt"),
                         (ROOT / "requirements-client.lock", "requirements-client.lock")):
        subprocess.run(["scp", *options, "-P", str(args.port), str(source),
                        f"{target}:{shlex.quote(remote + '/' + name + '.new')}"], check=True)
    # 原生密码扩展不能从 Windows zipapp 复制到 ARM 手机；在手机 venv 安装适配包。
    # 不改系统 Python，不复制设备私钥；安装失败时现有应用文件尚未替换。
    remote_python(f"""
import subprocess, sys
from pathlib import Path
p = Path({remote!r})
runtime = p / '.venv-e2e'
if not (runtime / 'bin/python').exists():
    subprocess.run([sys.executable, '-m', 'venv', str(runtime)], check=True)
subprocess.run([str(runtime / 'bin/python'), '-m', 'pip', 'install', '--quiet',
                '-r', str(p / 'requirements-client.lock.new')], check=True)
""")
    remote_interpreter = remote + "/.venv-e2e/bin/python"
    result = remote_python(f"""
import hashlib, json, os, ssl, subprocess, sys, zipfile
from pathlib import Path
p = Path({remote!r})
app = p / 'client.pyz.new'
assert sys.version_info >= (3, 12), 'Python >=3.12 required'
assert hashlib.sha256(app.read_bytes()).hexdigest() == {digest!r}
with zipfile.ZipFile(app) as z:
    assert z.testzip() is None
json.loads((p / 'client.json.new').read_text())
ssl.create_default_context(cafile=str(p / 'server.crt.new'))
subprocess.run(['openssl', 'version'], check=True, stdout=subprocess.DEVNULL)
subprocess.run([sys.executable, str(app), '--help'], check=True, stdout=subprocess.DEVNULL)
subprocess.run([sys.executable, '-c', 'from aioquic.quic.configuration import QuicConfiguration; QuicConfiguration()'], check=True)
for name in ('client.pyz', 'client.json', 'server.crt', 'requirements-client.lock'):
    stage = p / (name + '.new')
    os.chmod(stage, 0o600)
    stage.replace(p / name)
print(json.dumps({{'directory': str(p), 'files_bytes': sum(f.stat().st_size for f in p.iterdir() if f.is_file()), 'python': sys.version.split()[0], 'archive_sha256': {digest!r}}}))
""")
    print(result)
    # 在手机本机创建私钥，只取回公开申请；管理员应另行核实指纹后在服务器批准。
    request = remote_python(f"""
import subprocess, sys
from pathlib import Path
p = Path({remote!r})
if not (p / 'identity.key').exists():
    subprocess.run([sys.executable, str(p / 'client.pyz'), 'identity', '--node', {args.node!r},
                    '--key', str(p / 'identity.key'), '--request', str(p / 'identity.request.json')],
                   check=True, stdout=subprocess.DEVNULL)
print((p / 'identity.request.json').read_text())
""")
    (folder / "identity.request.json").write_text(request)
    print("Phone public approval request: " + str(folder / "identity.request.json"))


if __name__ == "__main__":
    main()
