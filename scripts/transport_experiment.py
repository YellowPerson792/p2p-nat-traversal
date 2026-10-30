"""Small loopback comparison: same load, controlled UDP loss and path outage.

No physical NAT claim. Run against any checkout exposing the Client API, and
keep only JSON results when comparing protocol revisions.
"""
import argparse
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import socket
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.environ.get("OVERLAY_SOURCE", str(ROOT)))
import uvicorn
from overlay.client import Client
from overlay.identity import approve, initialize, openssl
from overlay.server import create_app


class MeasuredTransport:
    def __init__(self, transport, loss_every=0):
        self.transport, self.loss_every = transport, loss_every
        self.packets = self.bytes = self.dropped = 0

    def sendto(self, data, address):
        self.packets += 1
        self.bytes += len(data)
        if self.loss_every and self.packets % self.loss_every == 0:
            self.dropped += 1
            return
        self.transport.sendto(data, address)

    def __getattr__(self, name):
        return getattr(self.transport, name)


@asynccontextmanager
async def environment():
    (ROOT / ".local").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="transport-", dir=ROOT / ".local") as directory:
        folder = Path(directory)
        registry, cert, key = folder / "members.json", folder / "server.crt", folder / "server.key"
        identities = {}
        for node, peer in (("a", "b"), ("b", "a")):
            identity, request = folder / (node + ".key"), folder / (node + ".request.json")
            public = initialize(node, identity, request)
            approve(registry, request, public["fingerprint"])
            identities[node] = str(identity)
        openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-keyout", key,
                "-out", cert, "-subj", "/CN=transport-test", "-addext", "subjectAltName=IP:127.0.0.1")
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        app = create_app(registry, "127.0.0.1", 0)
        runtime = uvicorn.Server(uvicorn.Config(app, log_level="critical", ssl_certfile=str(cert),
            ssl_keyfile=str(key), ws_max_size=16384))
        task = asyncio.create_task(runtime.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(5):
                while not runtime.started:
                    await asyncio.sleep(0.01)
            yield ({"server": "127.0.0.1", "control_port": listener.getsockname()[1],
                    "udp_port": app.state.coordinator.port, "ca": str(cert)}, identities)
        finally:
            runtime.should_exit = True
            await asyncio.wait_for(task, 5)
            listener.close()


async def scenario(name, config, identities):
    received, sent = {}, {}
    def receive(payload, path):
        index = int.from_bytes(payload[:8], "big")
        if index in sent:
            received[index] = (time.perf_counter() - sent[index]) * 1000
    mode = "relay-only" if name == "relay" else "auto"
    a = Client({**config, "identity": identities["a"]}, "a", "b", mode, receive)
    b = Client({**config, "identity": identities["b"]}, "b", "a", mode,
               on_data=lambda data, path: b.send(data))
    start_setup = time.monotonic()
    await a.start()
    await b.start()
    try:
        await asyncio.wait_for(asyncio.gather(a.ready.wait(), b.ready.wait()), 10)
        await asyncio.sleep(0.3)
        setup = time.monotonic() - start_setup
        a.transport = MeasuredTransport(a.transport, 10 if name == "loss" else 0)
        b.transport = MeasuredTransport(b.transport, 10 if name == "loss" else 0)
        cpu = time.process_time()
        start = time.monotonic()
        duration = 10 if name == "switch" else 3
        count = duration * 20
        for i in range(count):
            elapsed = time.monotonic() - start
            blocked = name == "switch" and 2 <= elapsed < 6
            was_blocked = a.drop_peer_udp
            a.drop_peer_udp = b.drop_peer_udp = blocked
            if was_blocked and not blocked:
                a.next_retry = b.next_retry = 0
            payload = i.to_bytes(8, "big") + bytes(248)
            if a.send(payload):
                sent[i] = time.perf_counter()
            await asyncio.sleep(max(0, start + (i + 1) / 20 - time.monotonic()))
        await asyncio.sleep(3)
        values = sorted(received.values())
        return {"scenario": name, "rate_pps": 20, "payload_bytes": 256, "setup_s": setup,
                "submitted": len(sent), "echoes": len(received), "scheduled": count,
                "mean_rtt_ms": statistics.mean(values) if values else None,
                "p95_rtt_ms": values[min(len(values)-1, int(len(values)*0.95))] if values else None,
                "cpu_s": time.process_time() - cpu,
                "client_udp_packets": a.transport.packets + b.transport.packets,
                "client_udp_bytes": a.transport.bytes + b.transport.bytes,
                "injected_drops": a.transport.dropped + b.transport.dropped,
                "paths": [row["path"] for row in a.metrics["path_changes"]]}
    finally:
        await asyncio.gather(a.close(), b.close())


async def main(args):
    async with environment() as (config, identities):
        config["delivery"] = args.delivery
        results = [await scenario(name, config, identities) for name in ("direct", "relay", "loss", "switch")]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"label": args.label, "scope": "loopback, no NAT; includes 3s drain, excludes setup from CPU/bytes", "results": results}, indent=2))
    print(json.dumps({"label": args.label, "output": str(args.output), "results": results}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--delivery", choices=("reliable", "datagram"), default="reliable")
    asyncio.run(main(parser.parse_args()))
