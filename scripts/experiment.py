"""Run real processes in the NAT lab and collect packet-level and application evidence."""
# 自动实验读取三种证据：客户端路径/回显、服务器转发计数、WAN 抓包。
# 三者对应“应用收到了什么”“服务器做了什么”“链路上看到了什么”，需结合判断。
import argparse
import asyncio
import contextlib
import json
import os
import signal
import ssl
import subprocess
import time
import urllib.request

from lab import ROOT, STATE, block, cleanup, prepare_config, setup
from overlay.identity import fetch_metrics

PYTHON = str(ROOT / ".venv/bin/python")


def https(config, path):
    if path == "/metrics":
        return fetch_metrics(config)
    ctx = ssl.create_default_context(cafile=config["ca"])
    req = urllib.request.Request(f'https://{config["server"]}:{config.get("control_port", 8443)}{path}',
                                 headers={})
    with urllib.request.urlopen(req, context=ctx, timeout=5) as response:
        return json.load(response)


async def wait_health(config):
    for _ in range(120):
        try:
            # The host reaches the simulated WAN through its uplink route.
            return await asyncio.to_thread(https, config, "/health")
        except Exception:
            await asyncio.sleep(0.5)
    raise RuntimeError("server health timeout")


async def spawn(ns, args, log_path, env=None):
    stream = log_path.open("w")
    try:
        process = await asyncio.create_subprocess_exec("ip", "netns", "exec", ns, *args,
                                                       cwd=ROOT, stdout=stream, stderr=stream,
                                                       env=env, start_new_session=True)
    finally:
        stream.close()
    return process


async def stop(process):
    if process and process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), 5)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            await process.wait()


async def wait_process(process, timeout):
    code = await asyncio.wait_for(process.wait(), timeout)
    if code:
        raise RuntimeError(f"Process {process.pid} exited {code}; inspect scenario logs")


async def scenario(folder, config_path, name, duration, mode="auto", initially_blocked=False, transitions=None, remote=False):
    folder = folder / name
    folder.mkdir(parents=True, exist_ok=True)
    config = json.loads(config_path.read_text())
    before = await asyncio.to_thread(https, config, "/metrics")
    # 起点快照用于算本轮中继增量；计数是全服务器共享的，实验应避免其他并发流量。
    block(initially_blocked, server=config["server"] if remote else None)
    processes = []
    try:
        capture = await spawn("ov-wan", ["tcpdump", "-i", "br0", "-U", "-nn", "udp", "-w", str(folder / "traffic.pcap")], folder / "capture.log")
        processes.append(capture)
        echo = await spawn("ov-b", [PYTHON, "-m", "scripts.diagnostics", "echo", "--config", str(config_path),
                                   "--node", "b", "--peer", "a", "--mode", mode,
                                   "--duration", str(duration + 15), "--output", str(folder / "echo")], folder / "echo.log")
        processes.append(echo)
        bench = await spawn("ov-a", [PYTHON, "-m", "scripts.diagnostics", "benchmark", "--config", str(config_path),
                                    "--node", "a", "--peer", "b", "--mode", mode,
                                    "--duration", str(duration), "--rate", "20", "--size", "256",
                                    "--output", str(folder / "benchmark")], folder / "benchmark.log")
        processes.append(bench)

        async def changes():
            started = time.monotonic()
            for when, enabled in transitions or []:
                await asyncio.sleep(max(0, started + when - time.monotonic()))
                block(enabled, server=config["server"] if remote else None)

        changer = asyncio.create_task(changes())
        try:
            await wait_process(bench, duration + 40)
            await changer
        finally:
            changer.cancel()
            await asyncio.gather(changer, return_exceptions=True)
        summary = json.loads((folder / "benchmark.json").read_text())
        after = await asyncio.to_thread(https, config, "/metrics")
        if before.get("instance") != after.get("instance"):
            raise RuntimeError("Server restarted during measurement; rerun for valid counter deltas")
        # 重启会使累计计数归零，所以必须先比较 instance，再计算增量。
        forwarded = after["relay_packets"] - before["relay_packets"]
        await stop(capture)
        direct_frames = None
        if not remote:
            # 本地按“不经过服务器的较大 UDP 包”筛选候选应用帧；这只是此拓扑/
            # 载荷下的辅助证据，不能把包长过滤器当成通用的协议识别或验签器。
            decoded = subprocess.run(["tcpdump", "-nnq", "-r", str(folder / "traffic.pcap"),
                                      "udp and not host 198.18.0.1 and greater 100"],
                                     check=True, capture_output=True, text=True)
            direct_frames = len(decoded.stdout.splitlines())
        result = {"scenario": name, "remote": remote, "server_instance": after.get("instance"),
                  "server_relay_packets": forwarded,
                  "captured_peer_data_frames": direct_frames, **summary}
        (folder / "result.json").write_text(json.dumps(result, indent=2))
        assert summary["echoes_received"] > 0, result
        paths = [x["path"] for x in summary["path_changes"]]
        if name == "direct":
            assert "direct" in paths and forwarded == 0, result
            assert summary["relay_received"] == 0, result
            assert direct_frames > 0, result
        if name in ("blocked-fallback", "remote-blocked-fallback"):
            assert "relay" in paths and forwarded > 0 and summary["direct_received"] == 0, result
            if not remote:
                assert direct_frames == 0, result
        if name == "remote-relay-control":
            assert forwarded > 0 and summary["relay_received"] > 0, result
        if name == "failover-recovery":
            # 要求 direct -> relay -> direct 的时间顺序，检查故障后能回退且能恢复。
            # 路径变化本身不证明可靠性；仍需检查回显及超时窗口内完成情况。
            direct = paths.index("direct")
            relay = paths.index("relay", direct + 1)
            assert "direct" in paths[relay + 1:] and forwarded > 0, result
        print(json.dumps({"scenario": name, "echoes": summary["echoes_received"],
                          "loss": summary["loss_fraction"], "paths": paths,
                          "relay_packets": forwarded}), flush=True)
        return result
    finally:
        for process in reversed(processes):
            await stop(process)
        block(False)
        await asyncio.sleep(1)


async def restart_scenario(folder, config_path):
    # 重启 B 会创建新会话/配对，A 应丢弃旧端点与密钥后重新探测，避免旧状态卡死。
    folder = folder / "client-restart"
    folder.mkdir(parents=True, exist_ok=True)
    processes = []
    try:
        def echo_args():
            return [PYTHON, "-m", "scripts.diagnostics", "echo", "--config", str(config_path), "--node", "b", "--peer", "a", "--duration", "40"]
        echo = await spawn("ov-b", echo_args(), folder / "echo-first.log")
        processes.append(echo)
        bench = await spawn("ov-a", [PYTHON, "-m", "scripts.diagnostics", "benchmark", "--config", str(config_path),
                                    "--node", "a", "--peer", "b", "--duration", "18",
                                    "--output", str(folder / "benchmark")], folder / "benchmark.log")
        processes.append(bench)
        await asyncio.sleep(6)
        await stop(echo)
        await asyncio.sleep(2)
        echo2 = await spawn("ov-b", echo_args(), folder / "echo-second.log")
        processes.append(echo2)
        await wait_process(bench, 45)
        summary = json.loads((folder / "benchmark.json").read_text())
        paths = [x["path"] for x in summary["path_changes"]]
        assert paths.count("direct") >= 2 and summary["echoes_received"] > 0, summary
        (folder / "result.json").write_text(json.dumps(summary, indent=2))
        print(json.dumps({"scenario": "client-restart", "paths": paths}), flush=True)
        return summary
    finally:
        for process in reversed(processes):
            await stop(process)
        await asyncio.sleep(1)


async def main(args):
    if os.geteuid() != 0:
        raise RuntimeError("Run via wsl -u root")
    folder = ROOT / "artifacts" / time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    folder.mkdir(parents=True, exist_ok=True)
    server = None
    setup()
    try:
        # Host route is needed only for health and metrics access to the local server.
        subprocess.run(["ip", "route", "add", "198.18.0.0/24", "via", "198.19.0.2", "dev", "ov-uplink"], check=True)
        results = []
        if args.remote:
            config_path = (ROOT / args.remote).resolve()
            await wait_health(json.loads(config_path.read_text()))
            results.append(await scenario(folder, config_path, "remote-auto", 12, remote=True))
            results.append(await scenario(folder, config_path, "remote-blocked-fallback", 8, initially_blocked=True, remote=True))
            results.append(await scenario(folder, config_path, "remote-relay-control", 8, mode="relay-only", remote=True))
        else:
            config = prepare_config()
            config_path = STATE / "identity-client.json"
            env = dict(os.environ)
            server = await spawn("ov-wan", [PYTHON, "-m", "overlay", "server", "--cert", str(STATE / "server.crt"),
                                           "--key", str(STATE / "server.key"), "--registry", str(STATE / "members.json")], folder / "server.log", env)
            await wait_health(config)
            results.append(await scenario(folder, config_path, "direct", 8, mode="direct-only"))
            results.append(await scenario(folder, config_path, "blocked-fallback", 8, initially_blocked=True))
            results.append(await scenario(folder, config_path, "failover-recovery", 32, transitions=[(7, True), (18, False)]))
            results.append(await restart_scenario(folder, config_path))
        (folder / "results.json").write_text(json.dumps(results, indent=2))
        print("Results: " + str(folder), flush=True)
    finally:
        await stop(server)
        cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--remote", help="Path to remote client.json")
    asyncio.run(main(parser.parse_args()))
