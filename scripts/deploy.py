"""Package local sources, upload over SSH, deploy, and retrieve pinned client config."""
import argparse
import json
import subprocess
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(args):
    subprocess.run(args, check=True, cwd=ROOT)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True, help="Coordinator IPv4 address (no implicit deployment target)")
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args()
    import ipaddress
    ipaddress.ip_address(args.host)
    remote = "root@" + args.host
    options = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=15"]
    ssh = ["ssh", *options, remote]
    scp = ["scp", *options]
    if args.rollback:
        run([*ssh, "bash /opt/p2p-overlay/current/scripts/remote-install.sh rollback"])
        return
    folder = ROOT / ".local" / "deploy"
    folder.mkdir(parents=True, exist_ok=True)
    version = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    archive = folder / f"{version}.tgz"
    with tarfile.open(archive, "w:gz") as tar:
        for name in ("pyproject.toml", "requirements.lock", "overlay", "scripts/remote-install.sh", "scripts/p2p-overlay.service"):
            path = ROOT / name
            if path.is_dir():
                for source in path.rglob("*"):
                    if source.is_file() and source.suffix in {'.py', '.html', '.js', '.css'}:
                        tar.add(source, arcname=source.relative_to(ROOT).as_posix())
            elif path.exists():
                tar.add(path, arcname=name)
    stage = "/opt/p2p-overlay/incoming"
    run([*ssh, f"install -d -m 700 {stage}"])
    run([*scp, str(archive), f"{remote}:{stage}/{version}.tgz"])
    run([*scp, str(ROOT / "scripts/remote-install.sh"), f"{remote}:{stage}/install.sh"])
    run([*ssh, f"bash {stage}/install.sh deploy {version} {args.host}"])
    config_folder = ROOT / ".local" / "remote"
    config_folder.mkdir(parents=True, exist_ok=True)
    run([*scp, f"{remote}:/etc/p2p-overlay/server.crt", str(config_folder / "server.crt")])
    run([*scp, f"{remote}:/etc/p2p-overlay/server.json", str(config_folder / "server.json")])
    config = json.loads((config_folder / "server.json").read_text())
    # Relative CA paths work on both Windows and WSL when launched from the project root.
    config["ca"] = ".local/remote/server.crt"
    (config_folder / "server.json").write_text(json.dumps(config, indent=2))
    print(f"Deployed {version}; public server config: .local/remote/server.json; approve each node before use")


if __name__ == "__main__":
    main()
