# Real-phone bidirectional video retest, 2026-10-25

The USB cable was disconnected. Wireless ADB automatically discovered the paired phone,
then launched the existing Android Linux terminal and forwarded Debian SSH port 2222.
The Debian client archive and file experiment were updated to the current implementation.
Existing device identities and reciprocal peer pins were retained; the server was not changed.

The Windows client bound to Wi-Fi 192.0.2.13. Android kept Wi-Fi 192.0.2.12
for management while the temporary bridge bound data/control sockets to cellular network 101.
Default/public-IP probes and server control connections independently showed broadband
198.51.100.5 versus cellular 198.51.100.2. The restarted Debian VM used
192.0.2.7 via gateway 192.0.2.6; bridge sources were adjusted accordingly.

Both trials used auto mode. Relay authenticated briefly during setup, then direct was selected.
Both peers recorded zero application messages received via relay. Video bytes and latency
probes used overlay UDP/QUIC, not ADB or SSH. The cellular forwarding bridge remains in this topology.

Each direction transferred the same 57,952,254-byte video. Receiver completion includes
flush/fsync and matching SHA-256, with an additional independent destination hash.
Goodput excludes setup and idle probes. Mbps and MB/s use decimal units.

| Direction | Confirmed seconds | Mbps | MB/s | Idle RTT median | Loaded RTT median / p95 | Max delivery gap | Stalls >1s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Phone cellular -> PC Wi-Fi | 32.75 | 14.16 | 1.77 | 111.9 ms | 176.3 / 360.7 ms | 412.3 ms | 0 |
| PC Wi-Fi -> phone cellular | 82.99 | 5.59 | 0.70 | 120.0 ms | 122.7 / 292.0 ms | 288.6 ms | 0 |

RTT is application probe round-trip time, including software/queueing delay; it is not
one-way latency. Delivery gaps are observed file-chunk callback gaps, not packet loss.

These are two sequential runs, not a controlled capacity or transport comparison.
Earlier optimized two-PC trials placed both peers on Windows and used USB to the cellular
bridge. Their 18.36 / 7.75 Mbps results are contextual references, not like-for-like baselines.
This retest alone cannot separate cellular variability, guest CPU/scheduling, bridge
queues, storage and QUIC congestion behavior. No transport settings were tuned in this retest.

SHA-256: `a1db251abf31d98ee2211695b800216e613efcfe77593f13a4f52405db75cc5e`.

Evidence (ignored local artifacts; source manifests include the actual deployed sources):

- `artifacts/phone-video-forward-cellular-auto-20261025-022805/`
- `artifacts/phone-video-reverse-cellular-auto-20261025-022911/`
- `artifacts/wireless-phone-video-20261025-summary.json`

Temporary bridge instances were stopped by the drivers. Wi-Fi and wireless ADB remained
enabled; no global routes or server approval entries were modified.
