"""Verify the real TLS/authentication boundary and idle-session expiry."""
# 检查三条独立边界：不信任证书时 TLS 应失败；未经批准的节点应被拒绝；
# 不发应用心跳/UDP 注册时会话应超时。HTTPS 健康检查成功不证明 UDP 打洞可用。
import argparse
import asyncio
import json
from pathlib import Path
import secrets
import ssl
import sys
import urllib.error
import urllib.request

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from overlay.identity import fetch_metrics, node_config, proof


async def check(config):
    context = ssl.create_default_context(cafile=config["ca"])
    base = f'https://{config["server"]}:{config.get("control_port", 8443)}'
    url = base.replace("https:", "wss:") + "/ws"
    result = {}

    def request(path, ctx):
        req = urllib.request.Request(base + path)
        with urllib.request.urlopen(req, context=ctx, timeout=10) as response:
            return json.load(response)

    result["health"] = await asyncio.to_thread(request, "/health", context)
    try:
        await asyncio.to_thread(request, "/health", ssl.create_default_context())
    except urllib.error.URLError as exc:
        assert isinstance(exc.reason, ssl.SSLCertVerificationError), exc
        result["untrusted_certificate_rejected"] = True
    else:
        raise AssertionError("Unexpectedly trusted the self-signed server without pin")
    try:
        await asyncio.to_thread(request, "/metrics", context)
    except urllib.error.HTTPError as exc:
        assert exc.code == 401
        result["unauthenticated_metrics_rejected"] = True
    else:
        raise AssertionError("Legacy HTTP metrics endpoint exposed group information")
    async with connect(url, ssl=context, proxy=None,
                       additional_headers={"Authorization": "Bearer old-token"}) as ws:
        await ws.send(json.dumps({"node": "unapproved-" + secrets.token_hex(6), "peer": "unused-peer"}))
        try:
            await ws.recv()
            raise AssertionError("Unapproved identity received an authentication challenge")
        except ConnectionClosed as exc:
            assert exc.rcvd and exc.rcvd.code == 4003
            result["unapproved_identity_rejected"] = True
    before = await asyncio.to_thread(fetch_metrics, config)
    node, peer = config.get("test_nodes", ["a", "b"])
    test_config = node_config(config, node)
    async with connect(url, ssl=context, proxy=None) as ws:
        await ws.send(json.dumps({"node": node, "peer": peer}))
        challenge = json.loads(await ws.recv())
        response = await asyncio.to_thread(proof, test_config["identity"], challenge, node, peer, "connect")
        await ws.send(json.dumps(response))
        welcome = json.loads(await ws.recv())
        assert welcome["kind"] == "welcome"
        try:
            await asyncio.wait_for(ws.recv(), welcome["ttl"] + 5)
            # WebSocket 自身可能有 ping/pong，但本项目只用 JSON heartbeat 或
            # UDP register 刷新 last_seen；底层连接活着并不代表会话被续期。
            raise AssertionError("Silent session received unexpected control data")
        except ConnectionClosed as exc:
            assert exc.rcvd and exc.rcvd.code == 4000
            result["idle_session_expired"] = True
            result["ttl_seconds"] = welcome["ttl"]
    after = await asyncio.to_thread(fetch_metrics, config)
    assert before["instance"] == after["instance"], "Server restarted during check"
    assert after["expired_sessions"] > before["expired_sessions"]
    assert node not in after["online_nodes"]
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=".local/remote/identity-client.json")
    parser.add_argument("--output", default="artifacts/remote-check.json")
    args = parser.parse_args()
    result = asyncio.run(check(json.loads(Path(args.config).read_text())))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
