import asyncio
import json
import time
from overlay.client import Client
from overlay.wire import log

# Historical diagnostic backpressure limit. Keep this tool independent from
# the secure transport's byte-window constants.
MAX_PENDING = 512


async def run_client(args, config):
    # echo/benchmark 是应用层测试，不是 ICMP ping；测量包含客户端调度和回显开销。
    pending = {}
    rows = []
    client = None
    deferred_echoes = set()

    async def delayed_echo(payload, allocation_id):
        try:
            await asyncio.wait_for(client.ready.wait(), 5)
            # 双方路径状态可能短暂不同步；等待本端可发送后再回显，但不跨配对代次。
            if client.allocation_id == allocation_id:
                client.send(payload)
        except asyncio.TimeoutError:
            pass

    def receive(payload, path):
        if args.command == "echo":
            # 暂不可发送时保留有界的延迟回显任务，避免等待队列无限增长。
            if not client.send(payload) and len(deferred_echoes) < MAX_PENDING:
                task = asyncio.create_task(delayed_echo(payload, client.allocation_id))
                deferred_echoes.add(task)
                client.tasks.add(task)
                task.add_done_callback(deferred_echoes.discard)
                task.add_done_callback(client.tasks.discard)
        elif args.command == "benchmark":
            # 载荷前 8 字节是应用测试编号，用于匹配回显；它与协议层 seq 不同。
            # RTT 在同一机器用单调时钟测量，无须两端时钟同步，也不能直接当作单向延迟。
            number = int.from_bytes(payload[:8], "big")
            entry = pending.pop(number, None)
            if entry:
                sent, elapsed, outbound_path = entry
                rows.append({"sequence": number, "elapsed": elapsed,
                             "rtt_ms": (time.perf_counter() - sent) * 1000,
                             "outbound_path": outbound_path, "return_path": path,
                             "bytes": len(payload)})
        else:
            log("message", node=args.node, path=path, text=payload.decode(errors="replace"))

    client = Client(config, args.node, args.peer, args.mode, receive)
    await client.start()
    begun = time.perf_counter()
    sent_count = 0
    attempted = 0
    setup = None
    try:
        await asyncio.wait_for(client.ready.wait(), args.connect_timeout)
        # setup 是从 start() 完成到路径 ready 的等待时间，不是首个回显的耗时。
        setup = time.perf_counter() - begun
        log("application_ready", node=args.node, setup_seconds=setup, path=client.path)
        if args.command == "benchmark":
            start = time.perf_counter()
            index = 0
            while time.perf_counter() - start < args.duration:
                now = time.perf_counter()
                payload = index.to_bytes(8, "big") + bytes(args.size - 8)
                if client.send(payload):
                    pending[index] = (time.perf_counter(), now - start, client.path)
                    sent_count += 1
                index += 1
                attempted = index
                # 以统一起点计算下一个发送时刻，避免每次固定 sleep 累积调度漂移。
                await asyncio.sleep(max(0, start + index / args.rate - time.perf_counter()))
            # 发送结束后继续接收 drain 秒；超过该观察窗口才到的回显不会计入结果。
            await asyncio.sleep(args.drain)
        elif args.command == "client" and args.message is not None:
            client.send(args.message.encode())
            await asyncio.sleep(args.duration)
        else:
            await asyncio.sleep(args.duration)
    except asyncio.TimeoutError:
        log("connect_timeout", node=args.node)
        raise
    finally:
        await client.close()
        summary = {"node": args.node, "mode": args.mode, "setup_seconds": setup,
                   **client.metrics}
        if args.command == "benchmark":
            import statistics
            # loss_fraction 是观察窗口内未完成回显的比例；可靠模式下也包含
            # 尚在恢复中的消息，不等价于底层 UDP 丢包率。
            # unavailable_slots 单列无路径时跳过的发送机会，不计为已发送报文丢失。
            # goodput 仅统计成功回显的应用字节 / 发送时长，不含协议头、探测、
            # 中继封装及回程的额外带宽，因而不是链路总吞吐率。
            summary.update({"packets_sent": sent_count, "echoes_received": len(rows),
                            "scheduled_packets": attempted, "unavailable_slots": attempted - sent_count,
                            "loss_fraction": (sent_count - len(rows)) / sent_count if sent_count else None,
                            "mean_rtt_ms": statistics.mean(r["rtt_ms"] for r in rows) if rows else None,
                            "goodput_bps": len(rows) * args.size * 8 / args.duration,
                            "payload_bytes": args.size, "rate_pps": args.rate})
            summary["rtt_by_path"] = {}
            for path_name in ("direct", "relay"):
                # 只比较去程和回程路径一致的 RTT，排除切换期间混合路径的样本。
                samples = [row["rtt_ms"] for row in rows
                           if row["outbound_path"] == row["return_path"] == path_name]
                summary["rtt_by_path"][path_name] = {
                    "echoes": len(samples), "mean_ms": statistics.mean(samples) if samples else None}
        log("summary", **summary)
        if args.output:
            import csv
            from pathlib import Path
            path = Path(args.output)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.with_suffix(".json").write_text(json.dumps(summary, indent=2))
            with path.with_suffix(".csv").open("w", newline="") as stream:
                fields = ["sequence", "elapsed", "rtt_ms", "outbound_path", "return_path", "bytes"]
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
