"""Two native processes, two mappings on the actual NAT; no network simulation."""
# 同一台机器开两个客户端，观察两个 UDP 端口经过实际路由器后的行为。
# 若二者共用 NAT，访问彼此公网端点是在测 NAT 回环；两个映射不等于两台 NAT。
import argparse
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from overlay.identity import fetch_metrics


def metrics(config):
    return fetch_metrics(config)


def outbound_address(config):
    # UDP connect selects a route but sends no packets and creates no NAT mapping.
    # UDP 的 connect 只设默认目的地并让 OS 选本地地址，没有 TCP 三次握手。
    # 真正发送 register 后，路由器才有机会创建可由服务器观察的 NAT 状态。
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect((config["server"], config.get("udp_port", 3478)))
        return probe.getsockname()[0]


def spawn(args, log_path):
    with log_path.open("w") as stream:
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        return subprocess.Popen([sys.executable, "-m", "scripts.diagnostics", *args], cwd=ROOT,
                                stdout=stream, stderr=stream, **options)


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def verify_mappings(a, b):
    # 先确认本地 socket、服务器所见映射各不相同，且候选确实互指，排除测错对象。
    # 相同公网 IP 可能还涉及运营商 NAT；这里不能据公网 IP 精确数出 NAT 设备。
    local_a, local_b = a["local_endpoint"], b["local_endpoint"]
    public_a, public_b = a["observed_endpoint"], b["observed_endpoint"]
    if not public_a or not public_b:
        raise RuntimeError("Missing server-observed NAT endpoint")
    assert local_a != local_b, "Clients must use separate UDP sockets"
    assert public_a != public_b, "Server must observe two distinct mappings"
    assert a["peer_endpoint"] == public_b and b["peer_endpoint"] == public_a, "Peer candidates must be server-observed endpoints"
    for endpoint in (public_a, public_b):
        assert ipaddress.ip_address(endpoint[0]).is_global, "Expected actual public NAT endpoints"
    return {"same_observed_public_ip": public_a[0] == public_b[0],
            "a": {"local": local_a, "public": public_a},
            "b": {"local": local_b, "public": public_b}}


def scenario(parent, config_path, config, name, duration, mode="auto", drop=False, ports=(0, 0)):
    folder = parent / name
    folder.mkdir()
    node_a, node_b = config.get("test_nodes", ["a", "b"])
    bind = outbound_address(config)
    common = ["--config", str(config_path), "--bind-host", bind, "--mode", mode]
    if drop:
        # 在客户端丢弃对端包是故障注入，用来验证回退；不能用它证明路由器过滤。
        common.append("--drop-peer-udp")
    before = metrics(config)
    processes = []
    try:
        echo = spawn(["echo", *common, "--bind-port", str(ports[1]), "--node", node_b, "--peer", node_a,
                      "--duration", str(duration + 5), "--output", str(folder / "echo")], folder / "echo.log")
        processes.append(echo)
        bench = spawn(["benchmark", *common, "--bind-port", str(ports[0]), "--node", node_a, "--peer", node_b,
                       "--duration", str(duration), "--rate", "20", "--size", "256",
                       "--output", str(folder / "benchmark")], folder / "benchmark.log")
        processes.append(bench)
        for process in (bench, echo):
            if process.wait(timeout=duration + 45):
                raise RuntimeError(f"{name}: process failed; inspect {folder}")
        a = json.loads((folder / "benchmark.json").read_text())
        b = json.loads((folder / "echo.json").read_text())
        after = metrics(config)
        assert before["instance"] == after["instance"], "Server restarted; retry measurement"
        # 只有同一服务器实例的累计计数才可相减；并发实验也会贡献此全局计数。
        forwarded = after["relay_packets"] - before["relay_packets"]
        mapping = verify_mappings(a, b)
        result = {"scenario": name, "topology": "two native processes behind the actual shared NAT",
                  "nat_simulation": False, "peer_udp_drop_injected": drop,
                  "pid_a": bench.pid, "pid_b": echo.pid, "python": sys.executable,
                  "mappings": mapping, "server_instance": after["instance"],
                  "server_relay_packets": forwarded, "benchmark": a, "echo": b}
        (folder / "result.json").write_text(json.dumps(result, indent=2))
        assert a["echoes_received"] > 0, "No application echoes received"
        if mode == "relay-only" or drop:
            assert forwarded > 0 and a["relay_received"] > 0 and not a["direct_received"]
        elif a["relay_received"] == 0:
            # auto 模式保留中继握手/保活；转发计数不再等价于应用数据走中继。
            assert a["direct_received"] > 0
        print(json.dumps({"scenario": name, "pid_a": bench.pid, "pid_b": echo.pid,
                          "mappings": mapping, "echoes": a["echoes_received"],
                          "sent": a["packets_sent"], "relay_packets": forwarded,
                          "paths": [x["path"] for x in a["path_changes"]]}), flush=True)
        return result
    finally:
        for process in reversed(processes):
            stop(process)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=".local/remote/identity-client.json")
    parser.add_argument("--duration", type=float, default=8)
    parser.add_argument("--port-a", type=int, default=0)
    parser.add_argument("--port-b", type=int, default=0)
    parser.add_argument("--scenario", choices=("all", "auto"), default="all")
    args = parser.parse_args()
    if args.duration <= 0:
        parser.error("duration must be positive")
    if not all(0 <= p <= 65535 for p in (args.port_a, args.port_b)) or (args.port_a and args.port_a == args.port_b):
        parser.error("Ports must be 0..65535 and fixed ports must be distinct")
    os.chdir(ROOT)
    config_path = Path(args.config).resolve()
    config = json.loads(config_path.read_text())
    folder = ROOT / "artifacts" / (time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + "-host")
    folder.mkdir(parents=True)
    ports = (args.port_a, args.port_b)
    results = [scenario(folder, config_path, config, "host-auto", args.duration, ports=ports)]
    if args.scenario == "all":
        results.extend([scenario(folder, config_path, config, "host-injected-fallback", args.duration, drop=True, ports=ports),
                        scenario(folder, config_path, config, "host-relay-control", args.duration, mode="relay-only", ports=ports)])
    (folder / "results.json").write_text(json.dumps(results, indent=2))
    print("Results: " + str(folder), flush=True)


if __name__ == "__main__":
    main()
