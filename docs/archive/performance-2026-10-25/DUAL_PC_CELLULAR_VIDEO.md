# Video between Wi-Fi and cellular clients on one PC

This is the pre-optimization baseline. See the subsequent
[bulk-transfer optimization](../../performance/BULK_TRANSFER_OPTIMIZATION.md) for the current
implementation and its repeat measurements.

Measured on 2026-10-25 (Europe/Stockholm), using two native Windows project
clients. One bound both control and UDP sockets to the PC Wi-Fi IPv4. The other
used the USB UDP adapter and the phone's cellular-bound sockets. The Pixel 10
remained connected to Wi-Fi. The phone Debian VM was not in the data path.

Both clients read or wrote local PC files. This tests the project's network
transport over the two Internet paths, rather than phone storage performance.
They still communicated through real NATs/Internet routes, not PC loopback.
The remote server observed their UDP endpoints at different public IPv4s:
cellular `198.51.100.2`, home broadband `198.51.100.5`. During each transfer,
server-side TCP socket inspection independently confirmed both control exits.

## Method and results

Each direction transferred the same **57,952,254-byte video** using the existing
reliable QUIC file protocol, 891 file bytes per message, with no tuning changes.
Directions refer to the client's chosen Internet path. Tests ran sequentially:
direct cellular upload, direct cellular download, relay cellular upload, relay
cellular download. Direct-only and relay-only modes fixed the intended path;
automatic switching was not exercised.

| Path | Direction | Confirmed goodput | Transfer time | Loaded RTT mean / p95 | Maximum delivery gap |
| --- | --- | ---: | ---: | ---: | ---: |
| Direct | Cellular → Wi-Fi | 7.57 Mbps | 61.28 s | 115.8 / 131.3 ms | 106.0 ms |
| Direct | Wi-Fi → cellular | 7.57 Mbps | 61.28 s | 113.5 / 130.8 ms | 853.7 ms |
| Forced relay | Cellular → Wi-Fi | 7.06 Mbps | 65.71 s | 126.1 / 143.3 ms | 205.9 ms |
| Forced relay | Wi-Fi → cellular | 6.49 Mbps | 71.46 s | 123.2 / 137.4 ms | 233.2 ms |

Goodput uses the sender's file-transfer start through the receiver's completion
acknowledgement; it excludes setup and the pre-transfer idle-probe period. RTT
is application probe/echo latency during load, not one-way latency. Delivery
gaps are between application file chunks, not IP packet arrival gaps.

All four receivers independently matched the source SHA-256:

`a1db251abf31d98ee2211695b800216e613efcfe77593f13a4f52405db75cc5e`

There were **zero delivery stalls above one second**. The sender's QUIC traces
declared 1, 11, 1 and 3 lost packets, respectively; these are QUIC recovery
events, not a direct measurement of physical network packet loss. Both sides'
path histories confirmed the intended direct or relay path for file delivery.
Receiver `peer_unavailable` events after transfer reflect sender shutdown.

## What appears to limit throughput

The application still caps outstanding reliable messages at **128**
(`overlay/secure.py`, `MAX_PENDING`). With 891 file bytes per message, at most
114,048 file bytes can await application acknowledgements. A rough window/RTT
limit is:

`128 × 891 × 8 / RTT_seconds`

Using measured loaded RTTs, this is about 7.88, 8.04, 7.24 and 7.41 Mbps for the
four runs. This estimate uses probe RTT as a proxy for application ACK RTT, so
it is an approximation, not an exact transport bound.

For the first three transfers, one-second load samples showed a median pending
count of 128; the window was exactly full in approximately 95%, 77% and 74% of
samples. The observed rates and frequent full windows support the application
window as a significant restriction. CPU work, QUIC congestion control, relay
processing and scheduling can impose additional limits. No window-size change
or causal performance tuning was attempted in this baseline.

Direct throughput was effectively symmetric in this setup. The earlier
phone/VM direction asymmetry was not reproduced here, but the device execution
environment, RTT and network conditions differ, so this does not isolate a
single cause of those earlier results. These single sequential runs also do
not establish maximum cellular capacity or a permanent relay penalty.

## Evidence and cleanup

Ignored evidence directory:
`artifacts/dual-pc-video-20261025-010528/`. It contains the aggregate report,
per-direction sender/receiver JSON, probe CSV, logs, independent control-exit
observations, received videos and bridge log. The local repeat driver is
`.local/video_dual_pc.py`.

The temporary reciprocally pinned test identities were removed from the server
registry; its original contents were verified restored. The UDP helper stopped
after the tests, and the phone's default network remained Wi-Fi (network 110).
No core project transport parameters or global routes were changed.
