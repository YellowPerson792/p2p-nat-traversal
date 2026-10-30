"""Public Ethernet / hotspot tests with per-socket interface pinning and capture."""
# 多网卡上“绑定地址”与“实际经物理接口出去”需要分别验证。
# 同机两个进程的包也可能在本机交付，所以同时抓物理接口和 loopback，检查方向与端口。
import argparse
from collections import Counter
import json
import subprocess
import time

from host_experiment import ROOT, metrics, spawn, stop
from overlay.wire import DIRECT_MAGIC, RELAY_MAGIC, read_direct, read_relay


def verify_capture(folder):
    # interface_id 是本次抓包中按添加顺序编号的接口：0/1 物理口、2 loopback，
    # 不是 Windows 网卡索引；若改变 capture_command 的接口顺序，这里也需对应调整。
    summary = json.loads((folder / "summary.json").read_text())
    packets = json.loads((folder / "packet-evidence.json").read_text())
    summary["loopback_test_packets"] = sum(r["packets"] for r in packets if r["interface_id"] == "2")
    for case in summary["cases"]:
        proof = {}
        for role, network in (("benchmark", case["benchmark_network"]), ("echo", case["echo_network"])):
            metrics_row = case["summaries"].get(role, {})
            expected_interface = "0" if network == "ethernet" else "1"
            selected = [r for r in packets if r["interface_id"] == expected_interface]
            local = metrics_row.get("local_endpoint")
            control = metrics_row.get("control_endpoint")
            proof[role] = {
                "udp_registration_on_expected_interface": bool(local) and any(
                    r["kind"] == "register" and r["source"] == local for r in selected),
                "tls_control_on_expected_interface": bool(control) and any(
                    r["kind"] == "tcp" and r["source"] == control for r in selected),
                "direct_data_out_on_expected_interface": bool(local) and any(
                    r["kind"] == "quic" and r["source"] == local for r in selected),
                "direct_data_in_on_expected_interface": bool(local) and any(
                    r["kind"] == "quic" and r["destination"] == local for r in selected)}
        case["capture_proof"] = proof
        direct_claim = case["summaries"].get("benchmark", {}).get("direct_received", 0) > 0
        case["physical_direct_path_verified"] = direct_claim and summary["loopback_test_packets"] == 0 and all(
            all(row.values()) for row in proof.values())
    (folder / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({"physical_direct_path_verified": [case["physical_direct_path_verified"] for case in summary["cases"]],
                      "loopback_test_packets": summary["loopback_test_packets"]}), flush=True)


def analyze(folder):
    # 抓包里的 kind 只是解析出的标签，未在这里验证 HMAC；应用验签/回显与
    # 抓包的接口、方向证据必须一起看，单凭一个标成 data 的包不证明直连成功。
    command = ["C:/Program Files/Wireshark/tshark.exe", "-r", str(folder / "traffic.pcapng"), "-T", "fields"]
    for field in ("frame.interface_id", "frame.interface_name", "ip.src", "udp.srcport", "tcp.srcport",
                  "ip.dst", "udp.dstport", "tcp.dstport", "ip.ttl", "udp.payload"):
        command.extend(["-e", field])
    output = subprocess.run(command, check=True, capture_output=True, text=True).stdout
    counts = Counter()
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) != 10:
            continue
        index, interface, src, usp, tsp, dst, udp, tcp, ttl, payload = fields
        if not src or not dst:
            continue
        kind, node, peer = "tcp", "", ""
        if usp:
            try:
                raw = bytes.fromhex(payload.replace(":", ""))
                if raw.startswith(DIRECT_MAGIC):
                    kind = "quic" if read_direct(raw)[-1] else "hole"
                elif raw.startswith(RELAY_MAGIC):
                    read_relay(raw)
                    kind = "relay"
                else:
                    body = json.loads(raw)
                    kind = body["kind"]
            except (ValueError, TypeError, KeyError):
                continue
        counts[(index, interface, src, usp or tsp, dst, udp or tcp, ttl, kind, node, peer)] += 1
    report = [{"interface_id": i, "interface": interface, "source": [src, int(sp)],
               "destination": [dst, int(dp)], "ttl": int(ttl), "kind": kind,
               "node": node, "peer": peer, "packets": count}
              for (i, interface, src, sp, dst, dp, ttl, kind, node, peer), count in sorted(counts.items())]
    (folder / "packet-evidence.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({"capture_kind_counts": dict(Counter({kind: sum(r["packets"] for r in report if r["kind"] == kind)
                                                          for kind in {r["kind"] for r in report}}))}), flush=True)


def run(args):
    config_path = (ROOT / args.config).resolve()
    config = json.loads(config_path.read_text())
    folder = ROOT / "artifacts" / (time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + "-multi-interface")
    folder.mkdir(parents=True)
    interfaces = {"ethernet": {"host": args.ethernet_host, "index": args.ethernet_index},
                  "hotspot": {"host": args.hotspot_host, "index": args.hotspot_index}}
    packet_filter = (f"(udp and portrange {args.port_base}-{args.port_base + 5}) or "
                     f"(tcp and host {config['server']} and port 8443)")
    capture_command = ["C:/Program Files/Wireshark/dumpcap.exe"]
    for interface in (args.ethernet_capture, args.hotspot_capture, r"\Device\NPF_Loopback"):
        capture_command.extend(["-i", interface, "-f", packet_filter])
    capture_command.extend(["-a", "duration:65", "-w", str(folder / "traffic.pcapng")])
    cases, processes = [], []
    with (folder / "capture.log").open("w") as stream:
        capture = subprocess.Popen(capture_command, stdout=stream, stderr=stream,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        time.sleep(0.7)
        if capture.poll() is not None:
            raise RuntimeError("Capture unavailable; inspect capture.log")
        schedule = [("ethernet", "hotspot", "auto"), ("hotspot", "ethernet", "auto"),
                    ("ethernet", "hotspot", "relay-only")]
        for index, (bench_network, echo_network, mode) in enumerate(schedule):
            case_folder = folder / str(index)
            case_folder.mkdir()
            bench_node, echo_node = config.get("test_nodes", ["a", "b"])
            before = metrics(config)

            def options(network, port):
                selected = interfaces[network]
                return ["--config", str(config_path), "--bind-host", selected["host"], "--interface-index",
                        str(selected["index"]), "--bind-port", str(port), "--mode", mode,
                        "--connect-timeout", "12"]

            echo = spawn(["echo", *options(echo_network, args.port_base + 1 + index * 2), "--node", echo_node,
                          "--peer", bench_node, "--duration", "10", "--output", str(case_folder / "echo")], case_folder / "echo.log")
            processes.append(echo)
            bench = spawn(["benchmark", *options(bench_network, args.port_base + index * 2), "--node", bench_node,
                           "--peer", echo_node, "--duration", "6", "--rate", "20", "--size", "256",
                           "--output", str(case_folder / "benchmark")], case_folder / "benchmark.log")
            processes.append(bench)
            codes = [process.wait(timeout=35) for process in (bench, echo)]
            summaries = {role: json.loads((case_folder / (role + ".json")).read_text())
                         for role in ("benchmark", "echo") if (case_folder / (role + ".json")).exists()}
            after = metrics(config)
            assert before["instance"] == after["instance"], "Server restarted"
            result = {"benchmark_network": bench_network, "echo_network": echo_network, "mode": mode,
                      "exit_codes": codes, "summaries": summaries,
                      "server_relay_packets": after["relay_packets"] - before["relay_packets"]}
            cases.append(result)
            (folder / "summary.json").write_text(json.dumps({"interfaces": interfaces, "cases": cases,
                "router_changes": False, "global_route_changes": False, "remote_deployment": False}, indent=2))
            print(json.dumps({"case": index, "benchmark_network": bench_network, "mode": mode,
                              "exit_codes": codes, "server_relay_packets": result["server_relay_packets"],
                              "benchmark": {k: summaries.get("benchmark", {}).get(k) for k in (
                                  "local_endpoint", "observed_endpoint", "peer_endpoint", "control_endpoint",
                                  "echoes_received", "direct_received", "relay_received", "mean_rtt_ms")}}), flush=True)
            for process in (bench, echo):
                stop(process)
                processes.remove(process)
    finally:
        try:
            for process in processes:
                stop(process)
        finally:
            try:
                capture.wait(timeout=70)
            except subprocess.TimeoutExpired:
                stop(capture)
    analyze(folder)
    verify_capture(folder)
    print("Results: " + str(folder), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=".local/remote/identity-client.json")
    parser.add_argument("--port-base", type=int, default=52031)
    for network in ("ethernet", "hotspot"):
        parser.add_argument("--" + network + "-host", required=True)
        parser.add_argument("--" + network + "-index", type=int, required=True)
        parser.add_argument("--" + network + "-capture", required=True)
    run(parser.parse_args())
