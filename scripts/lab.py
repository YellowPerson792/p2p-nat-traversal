"""Isolated two-NAT lab. Run with WSL root; never flush host firewall rules."""
# 拓扑：A(10.201.1.2) -> NAT-A(.1 / 198.18.0.2) -> br0 <- NAT-B <- B。
# ov-a/ov-b 是客户端命名空间，ov-na/ov-nb 是各自路由器，ov-wan 模拟外网。
# 命名空间各有路由表和防火墙；veth 像连接两台机器的一对网线接口，bridge 像交换机。
# 198.18.* 在这里是实验地址，不是真公网。此实验能验证流程，不能代表所有家用 NAT。
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from overlay.identity import initialize, approve, write_json

STATE = ROOT / ".local/lab"
NAMES = ["ov-a", "ov-b", "ov-na", "ov-nb", "ov-wan"]


def run(*args, ns=None, input=None, check=True):
    cmd = list(args)
    if ns:
        cmd = ["ip", "netns", "exec", ns, *cmd]
    return subprocess.run(cmd, input=input, text=True, check=check, capture_output=True)


def ip(*args, ns=None):
    return run("ip", *args, ns=ns)


def nft(text, ns=None):
    return run("nft", "-f", "-", ns=ns, input=text)


def pair(left, left_if, right, right_if, tag):
    ip("link", "add", tag + "a", "type", "veth", "peer", "name", tag + "b")
    for ns, temporary, final in ((left, tag + "a", left_if), (right, tag + "b", right_if)):
        if ns:
            ip("link", "set", temporary, "netns", ns)
        ip("link", "set", temporary, "name", final, ns=ns)
        ip("link", "set", final, "up", ns=ns)


def address(ns, device, value):
    ip("addr", "add", value, "dev", device, ns=ns)


def prepare_config():
    STATE.mkdir(parents=True, exist_ok=True)
    cert, key = STATE / "server.crt", STATE / "server.key"
    if not cert.exists():
        run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "365",
            "-keyout", str(key), "-out", str(cert), "-subj", "/CN=overlay-lab",
            "-addext", "subjectAltName=IP:198.18.0.1,IP:127.0.0.1")
    # 只有隔离的本地实验自动批准固定测试身份；真实服务器必须由管理员核实后批准。
    clients = {}
    registry = STATE / "members.json"
    for node in ('a', 'b', 'observer'):
        key, request = STATE / "identities" / (node + ".key"), STATE / "identities" / (node + ".request.json")
        if not key.exists():
            initialize(node, key, request)
        public = json.loads(request.read_text())
        approve(registry, request, public["fingerprint"], metrics=node == "observer")
        clients[node] = str(key)
    config = {"server": "198.18.0.1", "ca": str(cert), "control_port": 8443, "udp_port": 3478,
              "node": "observer", "identity": clients["observer"], "clients": clients, "test_nodes": ["a", "b"]}
    write_json(STATE / "identity-client.json", config)
    return config


def setup():
    existing = {line.split()[0] for line in run("ip", "netns", "list").stdout.splitlines()}
    if existing.intersection(NAMES) or Path("/sys/class/net/ov-uplink").exists():
        raise RuntimeError("Lab resources already exist; use cleanup before setup")
    STATE.mkdir(parents=True, exist_ok=True)
    old = Path("/proc/sys/net/ipv4/ip_forward").read_text().strip()
    (STATE / "forwarding.json").write_text(json.dumps({"previous": old}))
    try:
        for name in NAMES:
            ip("netns", "add", name)
            ip("link", "set", "lo", "up", ns=name)
        ip("link", "add", "br0", "type", "bridge", ns="ov-wan")
        ip("link", "set", "br0", "up", ns="ov-wan")
        address("ov-wan", "br0", "198.18.0.1/24")
        for suffix, subnet, wan_ip in (("a", "10.201.1", "198.18.0.2"), ("b", "10.202.1", "198.18.0.3")):
            # 客户端默认路由先到自己的 NAT；NAT 的外侧接到共享 WAN 网桥。
            # 开启 ip_forward 才会跨接口转发，仅给接口分配 IP 不会自动变成路由器。
            client, nat = "ov-" + suffix, "ov-n" + suffix
            pair(client, "eth0", nat, "lan", "ovl" + suffix)
            pair(nat, "wan", "ov-wan", "port" + suffix, "ovw" + suffix)
            ip("link", "set", "port" + suffix, "master", "br0", ns="ov-wan")
            address(client, "eth0", subnet + ".2/24")
            address(nat, "lan", subnet + ".1/24")
            address(nat, "wan", wan_ip + "/24")
            ip("route", "add", "default", "via", subnet + ".1", ns=client)
            ip("route", "add", "default", "via", "198.18.0.1", ns=nat)
            run("sysctl", "-qw", "net.ipv4.ip_forward=1", ns=nat)
            # SNAT 在出站时把私网源地址改成 wan_ip；conntrack 记录转换，回包时还原。
            # allowed 则额外模拟严格过滤：只允许曾经主动联系过的远端 IP/端口回包。
            # 因而 A 只向服务器注册，并不会顺带允许 B 发包进来；A 还得向 B 发探测。
            # permit_return 在 SNAT 之后记录三元组（远端 IP、远端端口、本端外部端口），
            # ingress 在反向转换之前用回包的源 IP、源端口、目的端口匹配它。
            nft(f'''table ip overlay_nat {{
 set allowed {{ type ipv4_addr . inet_service . inet_service; flags dynamic,timeout; timeout 60s; }}
 chain ingress {{ type filter hook prerouting priority raw; policy accept;
  iifname "wan" ip protocol udp ip saddr . udp sport . udp dport != @allowed drop
 }}
 chain post {{ type nat hook postrouting priority srcnat; policy accept;
  oifname "wan" snat to {wan_ip}
 }}
 chain permit_return {{ type filter hook postrouting priority 110; policy accept;
  oifname "wan" ip protocol udp update @allowed {{ ip daddr . udp dport . udp sport timeout 60s }}
 }}
 chain blocker {{ type filter hook forward priority -200; policy accept; }}
 chain forward {{ type filter hook forward priority filter; policy drop;
  ct state established,related accept
  iifname "lan" oifname "wan" accept
 }}
}}''', ns=nat)
        pair(None, "ov-uplink", "ov-wan", "uplink", "ovu")
        # uplink 让模拟 WAN 能访问真实远端服务器；下方两层 masquerade 动态使用
        # 出口 IP。远端实验因此还经过宿主网络，不能当作仅有两层 NAT 的证明。
        address(None, "ov-uplink", "198.19.0.1/30")
        address("ov-wan", "uplink", "198.19.0.2/30")
        ip("route", "add", "default", "via", "198.19.0.1", ns="ov-wan")
        run("sysctl", "-qw", "net.ipv4.ip_forward=1", ns="ov-wan")
        Path("/proc/sys/net/ipv4/ip_forward").write_text("1")
        nft('''table ip overlay_lab_host {
 chain post { type nat hook postrouting priority srcnat; policy accept;
  ip saddr 198.19.0.2 masquerade
 }
}''')
        nft('''table ip overlay_wan_nat {
 chain post { type nat hook postrouting priority srcnat; policy accept;
  oifname "uplink" ip saddr 198.18.0.0/24 masquerade
 }
}''', ns="ov-wan")
        prepare_config()
        print("Lab ready: A -> NAT-A -> simulated WAN <- NAT-B <- B")
    except Exception:
        cleanup()
        raise


def block(enabled, server=None):
    # 只切断对端 UDP，保留服务器通信，才能观察“直连失效但中继仍可用”。
    # blocker 优先于 established 放行规则，否则已建立的直连可能绕过故障注入。
    # NAT-side drop precedes established-flow acceptance; no bridge modules needed.
    for nat, other in (("ov-na", "198.18.0.3"), ("ov-nb", "198.18.0.2")):
        nft("flush chain ip overlay_nat blocker", ns=nat)
        if enabled:
            if server:
                import ipaddress
                ipaddress.ip_address(server)
                nft(f'''add rule ip overlay_nat blocker oifname "wan" ip protocol udp ip daddr != {server} counter drop
add rule ip overlay_nat blocker iifname "wan" ip protocol udp ip saddr != {server} counter drop''', ns=nat)
            else:
                nft(f'''add rule ip overlay_nat blocker ip protocol udp ip daddr {other} counter drop
add rule ip overlay_nat blocker ip protocol udp ip saddr {other} counter drop''', ns=nat)
    print("Peer UDP blocked" if enabled else "Peer UDP restored")


def cleanup():
    # 仅移除本实验资源并恢复先前的 IP 转发开关，不清空宿主的全局防火墙规则。
    existing = {line.split()[0] for line in run("ip", "netns", "list").stdout.splitlines()}
    for name in NAMES:
        if name in existing:
            processes = run("ip", "netns", "pids", name).stdout.split()
            for pid in processes:
                run("kill", "-TERM", pid, check=False)
            ip("netns", "delete", name)
    run("ip", "link", "delete", "ov-uplink", check=False)
    run("nft", "delete", "table", "ip", "overlay_lab_host", check=False)
    marker = STATE / "forwarding.json"
    if marker.exists():
        Path("/proc/sys/net/ipv4/ip_forward").write_text(json.loads(marker.read_text())["previous"])
        marker.unlink()
    print("Lab resources cleaned")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("setup", "block", "unblock", "cleanup"))
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Run in WSL as root (wsl -u root)")
    {"setup": setup, "block": lambda: block(True), "unblock": lambda: block(False), "cleanup": cleanup}[args.action]()


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(exc.stderr or str(exc))
        raise SystemExit(1)
