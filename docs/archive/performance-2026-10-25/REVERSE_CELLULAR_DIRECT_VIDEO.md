# PC-to-phone cellular direct video transfer, 2026-10-24

This is the original-bridge baseline. The later
[bridge performance audit](BRIDGE_PERFORMANCE.md) compares it with native VM
cellular forwarding (2.85 Mbps) and the optimized bridge (3.35 Mbps).

The same **57,952,254-byte video** was transferred from native Windows to the
phone with **direct-only** enabled at both endpoints. No relay fallback was
allowed. Independent SHA-256 computation on the phone matched the PC source.

## Network and path proof

The phone remained connected to Wi-Fi for wireless ADB/SSH management. Both its
control TCP and application UDP sockets were explicitly bound to cellular by the
Android helper. The ordinary Android default network stayed on Wi-Fi. Separate
public-IP probes and the coordinator's observed phone UDP address verified the
cellular exit. The PC retained its home Wi-Fi interface binding.

The receiver recorded **65,816 direct application messages
and zero relayed application messages**. The helper independently counted
**63,035,974 direct UDP downlink bytes**, compared with 63,048,745 total UDP
downlink bytes; the remainder was server registration/control UDP traffic. It
counted 4,944,230 direct UDP uplink bytes. QUIC connection setup, acknowledgements,
probes and overlay headers are included in these byte counts. They exclude
IP/UDP/link headers, TCP control traffic and phone billing overhead.

To enable direct traffic, the experimental local UDP bridge was extended from a
fixed-server forwarding format to an address-preserving format: a nine-byte local
header carries the original IPv4 address and UDP port. This header is used only
between Debian and Android and is stripped before transmission over cellular.
The helper restricts remote destinations to the fixed server and the PC public
IP. The same cell-bound UDP socket contacts both server and peer, preserving the
source mapping used for hole punching. TLS, peer pins, HMAC and QUIC validation
remain in the existing client. No project transport policy was changed.

## Results

| Metric | Direct result | Previous reverse relay |
|---|---:|---:|
| Complete-file confirmed time | 183.99 s | 219.36 s |
| Mean effective file goodput | 2.52 Mbps | 2.11 Mbps |
| Decimal file transfer speed | 0.315 MB/s | 0.264 MB/s |
| Peak receiver one-second goodput | 5.23 Mbps | 5.96 Mbps |
| Loaded RTT median / P95 | 157.8 / 297.5 ms | 180.5 / 359.9 ms |
| Maximum file delivery gap | 1265.5 ms | 549.0 ms |
| Delivery gaps over one second | 1 | 0 |
| Cross-connection logical replays | 0 | 0 |
| Duplicate logical messages at receiver | 0 | 0 |
| Missing loaded RTT probe replies | 3 / 736 | 4 / 877 |
| Complete-file SHA-256 | match | match |

Idle RTT median/P95 was 158.5/281.7 ms. Loaded RTT mean was 174.6 ms and its
maximum was 863.5 ms. RTT is an application round trip measured at the PC,
including endpoint scheduling. Missing probe replies are excluded from latency
percentiles. File timing starts with the sender's first file submission and ends
with the receiver's validated, flushed/fsynced completion reply. Setup, source
hashing and idle probes are excluded from mean throughput timing.

## Interpretation

Direct was approximately **19% faster in average throughput** than the preceding
reverse relay run, but still much slower than the earlier phone-to-PC direct
run's 6.50 Mbps. These are single sequential observations, not a capacity limit
or an isolated attribution of directional asymmetry. The two reverse runs both
use Android forwarding; the direct run adds address metadata to that local
forwarding protocol. Neither can be directly equated to the earlier no-helper
VM test. Network variation, endpoint processing and flow/congestion control
remain variables.

The PC sender recorded 245 native QUIC loss declarations among 54,019 sent QUIC
packets. Sampled congestion windows during file submission ranged from 28,961 to
122,996 bytes. This supports examining the same window/recovery mechanisms as in
previous tests, but does not establish a physical packet-loss rate or isolate
radio, route or scheduling causes. A single 1.266-second delivery gap occurred
without a path change. The final `waiting` event followed normal peer shutdown.

## Phone file and evidence

The completed file is in Android **Download**:
`overlay-received-from-pc-direct-20261024-232440.mp4`.
Its independent SHA-256 is:

```text
a1db251abf31d98ee2211695b800216e613efcfe77593f13a4f52405db75cc5e
```

Ignored `artifacts/phone-video-reverse-cellular-direct-20261024-232440/` contains
sender/receiver JSON, PC RTT CSV, both progress logs, route/interface observations,
server TCP peers, independent phone hash validation and helper source snapshots.
The video was not returned through management SSH for validation. The temporary
bridge was stopped and no test process remains running. Wi-Fi and wireless ADB
remain connected.
