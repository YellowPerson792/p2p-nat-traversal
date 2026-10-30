# PC traffic through the phone's cellular network

These temporary Android helpers let the PC use cellular while the phone remains
connected to Wi-Fi. Use **overlay UDP mode for this project**; SOCKS5 mode below
is an additional option for ordinary TCP applications.

## Project UDP mode

Enable USB tethering and USB debugging. From the repository root, use the current
phone USB gateway and PC USB IPv4 addresses (the example addresses may change):

```powershell
powershell -ExecutionPolicy Bypass -File scripts/android-cellular/Start-CellularProxy.ps1 -OverlayUdp -PhoneAddress 192.0.2.9 -PcAddress 192.0.2.8 -ServerAddress 198.51.100.1 -PeerAddress 198.51.100.5
```

`ServerAddress` selects the project's coordinator/relay. `PeerAddress` must be the
other peer's public IPv4 for direct traffic. Omit it for relay-only use. Override
`ServerUdpPort` / `ServerControlPort` if not using 3478 / 8443. The listener only
accepts the configured PC source IP and destinations: the server UDP port and,
when configured, the peer IP. It is not a general UDP relay.

In another terminal, run the project through the adapter:

```powershell
.venv-win/Scripts/python.exe scripts/android-cellular/overlay_via_cellular.py --gateway 192.0.2.9 client --config .local/remote/pc.json --node pc --peer phone --mode auto --control-port 8765
```

The adapter supports the production `client` proxy command and its browser interface.
Historical echo/benchmark results below describe the separate diagnostic applications.
For the project's file experiment, insert `--file-experiment` before `send` or
`receive`, then supply the usual file-experiment arguments:

```powershell
.venv-win/Scripts/python.exe scripts/android-cellular/overlay_via_cellular.py --gateway 192.0.2.9 --file-experiment send --config .local/remote/pc.json --node pc --peer phone --mode relay-only --file video.mp4 --output artifacts/file-cellular.json
```

UDP takes this path:

`project -> UDP/USB -> phone:49170 -> cellular-bound UDP socket -> peer or relay`

The local datagram adds 9 bytes (`OC1`, destination IPv4, port). The phone strips
them before sending. Replies carry the original sender's IPv4/port back to the
adapter, so the project's direct/relay selection, packet authentication and QUIC
remain intact. Each PC UDP endpoint gets a separate cellular socket/NAT mapping.
**UDP is never tunneled through TCP or ADB.** Up to 16 endpoints are retained;
idle mappings expire after 60 seconds. Payloads above 8192 bytes are rejected.

Control TLS/WebSocket uses phone TCP port 49171, forwarded only to the specified
server control port. The adapter preserves the real TLS URL and certificate
verification. Existing `bind_host` / `interface_index` overrides are superseded
inside the adapter, so control traffic cannot preconnect to the server through
the old interface. The USB gateway determines the local route instead.

No core overlay code or global routes are changed. The adapter must be used
explicitly; an ordinary `python -m overlay` invocation bypasses it. Cellular
loss fails traffic instead of falling back to Wi-Fi. This is not an IP VPN.
Direct traffic requires updating `PeerAddress` if the peer's public IP changes.

Stop UDP mode from another shell (or wait for its 600-second lifetime):

```powershell
& "$env:LOCALAPPDATA/Android/Sdk/platform-tools/adb.exe" -d shell touch /data/local/tmp/overlay-cellular-udp.stop
```

### UDP verification on 2026-10-25

The later [full-video test in both directions](../../docs/archive/performance-2026-10-25/DUAL_PC_CELLULAR_VIDEO.md)
uses two PC clients over Wi-Fi and cellular, with direct and forced-relay runs.

Two isolated, reciprocally pinned test identities ran on the PC. One used the
USB/cellular adapter and the other used the PC Wi-Fi interface. The phone stayed
connected to Wi-Fi throughout. Coordinator-observed UDP addresses were cellular
`198.51.100.2` for the adapter and home broadband `198.51.100.5` for the peer.

| Project delivery mode | Path | Completed echoes | Mean application RTT |
| --- | --- | ---: | ---: |
| QUIC reliable | Relay only | 40/40 | 117.4 ms |
| QUIC reliable | Direct only | 41/41 | 114.7 ms |
| QUIC Datagram | Relay only | 41/41 | 121.9 ms |

The direct-only run recorded zero relayed received messages. A subsequent
1,048,576-byte file transfer through the same adapter and QUIC relay completed
with matching receiver and source SHA-256. Its sender-confirmed duration was
1.64 seconds (5.11 Mbps); this small functional test does not measure sustained
capacity. The wrapper's file-argument forwarding was corrected before that
successful file run and is covered by a regression test.

The three echo summaries are in ignored
`artifacts/pc-udp-cellular-20261025-005711/`; the successful file test and its
report are in `artifacts/pc-udp-cellular-20261025-005913/`. Temporary test registry
entries were removed after each trial, with the original registry content
verified restored. The helper stopped with Android's default network still 110
(Wi-Fi), reporting 1,332,619 outbound and 176,046 inbound UDP payload bytes.

Adapter and existing client/network-binding checks passed: `16 passed` using
`pytest tests/test_cellular_adapter.py tests/test_client.py tests/test_network_binding.py`.
This validates the tested direct/relay routes; automatic path switching and
cellular handover recovery were not tested in this extension.

Both Android helpers use a debug-only framework bootstrap, need an already
active cellular network, and stop after their configured lifetime. They do not
automatically restart or reacquire a replacement cellular network. The local
UDP envelope is not encrypted or authenticated separately; it is restricted to
the configured USB interface/client, while the project's authentication and
end-to-end encryption remain responsible for protecting overlay messages.

## Ordinary TCP applications

The route is `PC -> USB (or ADB forwarding) -> Android proxy -> cellular -> destination`.
Android's `Network.bindSocket()` selects cellular before each TCP connection.
SOCKS hostname requests are resolved using that same Android network. The proxy
copies bytes without terminating HTTPS or SSH encryption. It does not change the
PC default route, Android default network, or system proxy settings.

### Start SOCKS5 mode

Requires USB debugging authorized on the phone, an active cellular Internet
network, Android SDK platform/build tools, and JDK 17 on the PC. Run from the
repository root. Keep only one instance running.

The simplest mode exposes a PC-local SOCKS port through USB ADB:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/android-cellular/Start-CellularProxy.ps1
```

In another terminal, use remote DNS (`socks5h`, not `socks5`):

```powershell
curl.exe --noproxy "" --proxy socks5h://127.0.0.1:1080 https://api4.ipify.org
```

Alternatively, bypass ADB in the data path and connect over USB tethering.
Enable USB tethering, find the current phone gateway and PC USB IPv4 addresses,
then supply them explicitly. The addresses below were valid for the test only:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/android-cellular/Start-CellularProxy.ps1 -DirectUsb -PhoneAddress 192.0.2.9 -PcAddress 192.0.2.8
curl.exe --noproxy "" --proxy socks5h://192.0.2.9:49172 https://api4.ipify.org
```

The phone listener binds only to the supplied USB address and accepts only the
specified PC source IP. The default ADB mode binds to Android loopback. This is
a temporary debug proxy without SOCKS authentication; it is not a public proxy.

The foreground launcher removes its ADB port mapping when it exits. The phone
helper stops after 600 seconds by default (`-Seconds 1..900`), closing active
connections. To stop it earlier from another terminal:

```powershell
& "$env:LOCALAPPDATA/Android/Sdk/platform-tools/adb.exe" -d shell touch /data/local/tmp/overlay-cellular-proxy.stop
```

For SSH, `socks_connect.py` is an optional standard-library ProxyCommand helper:

```powershell
ssh -o "ProxyCommand=.venv-win/Scripts/python.exe scripts/android-cellular/socks_connect.py %h %p" user@host
```

Add `--proxy-host PHONE_USB_IP --proxy-port 49172` inside that ProxyCommand for
direct USB mode. Keep normal SSH host-key verification enabled.

### SOCKS5 verification on 2026-10-25

The Pixel 10 was connected to Wi-Fi. Its default network was 110 and default
public IPv4 was `198.51.100.5`. Cellular network 101 (`rmnet16`) had public IPv4
`198.51.100.2`. Both PC proxy modes returned the cellular public IP; the remote
SSH server independently observed that same source. Android's default network
remained 110 at proxy shutdown.

Single-stream SSH/TCP measurements, compression disabled, 16 MiB per direction:

| PC access to phone proxy | Download to PC | Upload from PC |
| --- | ---: | ---: |
| USB ADB forwarding | 19.84 Mbps | 29.89 Mbps |
| USB tethering, direct socket | 19.93 Mbps | 24.79 Mbps |

Each receiver confirmed exactly 16,777,216 payload bytes. Timing excludes SSH
setup. These sequential short runs demonstrate operation; cellular conditions
vary, so their difference does not establish an ADB performance advantage.
Four concurrent HTTPS requests also returned the cellular exit. Unsupported
authentication, UDP ASSOCIATE, and invalid address types were rejected.
Evidence is retained in ignored `artifacts/tether-speed-20261025/proxy-*.json`.
Both proxy instances were stopped after verification.

### SOCKS5 limits

- TCP CONNECT only. UDP, QUIC, ICMP and transparent whole-PC routing are not
  implemented in SOCKS5 mode. Applications must explicitly support SOCKS; system
  proxy settings do not force all programs through it. Use the separate project
  UDP mode above for the overlay's QUIC traffic.
- Requires a currently active cellular network. Network loss fails connections;
  there is no Wi-Fi fallback or automatic cellular re-acquisition. DNS currently
  tries the first returned address without address racing.
- Uses debug-only Android framework bootstrap, tested on this Pixel 10. It is not
  an installed app, background service, VPN, or portable Android API integration.
- Bounded worker pool (64 threads); long connections consume workers. The
  lifetime limit deliberately terminates all sessions. PC/USB addresses can
  change after reconnecting.
