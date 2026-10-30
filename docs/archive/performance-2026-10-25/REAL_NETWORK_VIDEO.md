# Real phone-to-PC video transfer, 2026-10-24

The later PC-to-phone test, with cellular control/data routing while Wi-Fi stayed
connected for management, is recorded separately in
[the reverse cellular video report](REVERSE_CELLULAR_VIDEO.md).

The existing QUIC transport was deployed to the remote coordinator and Pixel 10
Debian client. The PC used native Windows Python 3.12; the phone used Debian ARM64
Python 3.13.5. Both endpoints independently generated their Ed25519 identities;
only reciprocal PC/phone communication was approved. Public-key fingerprints
were checked through USB SSH and pinned at both endpoints. Private keys stayed
on their devices. No transport selection or retransmission policy was changed.

## Method

The source was an existing 57,952,254-byte phone video (55.27 MiB, H.264,
1144 x 1208, 50.29 seconds, average file bitrate 9.22 Mbps). A temporary copy
inside phone shared storage made it readable to Debian. USB/SSH deployed and
launched the programs and retrieved reports; file payloads went through the
overlay. All received files were independently hashed on Windows. The complete
first Wi-Fi copy also passed a full FFmpeg decode without reported errors.

`scripts/file_experiment.py` uses the existing reliable `Client.send()` API:
891 file bytes + 1 type byte + 8 offset bytes fit its 900-byte limit. It obeys
the existing 128-message backpressure window. Receiver completion means size
and SHA-256 match, after file flush/fsync; sender timing runs from the first
submission through this completion reply. Setup, source hashing and the idle
probe period are excluded from throughput timing. MB/s and Mbps are decimal.

Unreliable encrypted 9-byte request/reply probes run every 250 ms, including
six seconds before file transmission. RTT is measured on the phone using one
monotonic clock: it includes both network directions and endpoint scheduling,
and is not one-way delay. Lost probes are excluded from RTT percentiles and
listed separately. Percentiles use the lower indexed sample. One-second receiver
throughput samples and logical delivery gaps measure application observations,
not packet arrival times. Native QUIC qlog events are counted without storing
packet contents. Instrumentation itself contributes CPU overhead.

The PC remained on home Wi-Fi. The first two complete runs had both devices on
the same Wi-Fi and used the relay. Before the cellular runs the user disabled
phone Wi-Fi; Android interface state and distinct observed public endpoints
confirmed the topology. Each row is one run, not a statistically repeated
capacity benchmark. Cellular runs did not overlap.

## Complete-file results

| Topology / mode | File path observed | Seconds | Mbps | MB/s | Idle RTT median / P95, ms | Loaded RTT median / P95, ms | SHA-256 |
|---|---|---:|---:|---:|---:|---:|---|
| Same Wi-Fi, auto | relay | 56.38 | 8.22 | 1.03 | 53.6 / 55.3 | 65.2 / 85.6 | match |
| Same Wi-Fi, relay only | relay | 59.88 | 7.74 | 0.97 | 55.1 / 60.6 | 69.0 / 90.6 | match |
| Cellular, auto | direct | 71.32 | 6.50 | 0.81 | 103.5 / 127.4 | 120.2 / 173.1 | match |
| Cellular, relay only | relay | 227.12 | 2.04 | 0.26 | 150.5 / 305.2 | 169.5 / 252.8 | match |
| Cellular, injected direct failure | direct + relay | 78.11 | 5.94 | 0.74 | 134.8 / 160.6 | 129.0 / 180.8 | match |
| Wi-Fi reconnected, relay only repeat | relay | 62.88 | 7.37 | 0.92 | 55.4 / 63.5 | 69.1 / 106.3 | match |
| Cellular reselected, relay only repeat | relay | 121.27 | 3.82 | 0.48 | 131.9 / 195.8 | 154.1 / 229.8 | match |

## Reliability, cost and latency tails

| Run | Max delivery gap, ms | Gaps >1s | Sender logical replays | Receiver duplicate messages | Missing idle / loaded probes | Sender QUIC loss events / packets sent | Endpoint UDP payload bytes / file bytes | Phone / PC process CPU seconds |
|---|---:|---:|---:|---:|---|---|---:|---|
| Same Wi-Fi, auto | 127.5 | 0 | 0 | 0 | 6/10 / 1/221 | 271 / 58920 | 1.194 | 41.75 / 25.67 |
| Same Wi-Fi, relay only | 299.3 | 0 | 0 | 0 | 0/25 / 1/234 | 459 / 60957 | 1.203 | 45.56 / 23.44 |
| Cellular, auto | 256.1 | 0 | 0 | 0 | 0/25 / 0/280 | 54 / 71376 | 1.148 | 51.06 / 13.30 |
| Cellular, relay only | 375.0 | 0 | 0 | 0 | 1/25 / 11/884 | 225 / 64333 | 1.211 | 139.07 / 34.16 |
| Cellular, injected direct failure | 3136.3 | 1 | 256 | 128 | 0/25 / 12/307 | 73 / 73537 | 1.176 | 54.35 / 15.73 |
| Wi-Fi reconnected, relay only repeat | 1075.5 | 1 | 0 | 0 | 0/25 / 0/245 | 491 / 62458 | 1.205 | 46.82 / 20.94 |
| Cellular reselected, relay only repeat | 355.5 | 0 | 0 | 0 | 0/25 / 3/473 | 70 / 70349 | 1.223 | 84.69 / 29.50 |

QUIC loss events are sender loss declarations, including potentially spurious
declarations and handshake/backup traffic; they are not a capture-based physical
packet loss measurement and do not count exact retransmitted file bytes.
Endpoint UDP bytes include QUIC, relay envelopes, probes, registration and warm
backup traffic over the whole measured process. They exclude IP/UDP/L2 headers,
TLS/WebSocket management and server forwarding egress. This is not total network
bandwidth. CPU seconds include setup, hashing, framing, file I/O, instrumentation
and idle/drain time, not just encryption and not a CPU percentage.

## Findings and limits

- Cellular auto mode established a real direct path; the same-Wi-Fi trials used
  the relay. Same Wi-Fi alone does not prove LAN direct reachability because the
  implementation exchanges server-observed endpoints and has no LAN discovery.
  No capture here establishes the router's exact hairpin/filtering behavior.
- The 128 x 891-byte outstanding file window is only about 114 KB. A rough
  window/RTT ceiling at 120 ms is 7.60 Mbps, before accounting for processing and
  timing effects. This is a plausible throughput constraint, not proof of the
  only bottleneck. It should be measured before blaming QUIC encryption.
- The source bitrate is about 9.22 Mbps. Complete, bit-identical file delivery
  does not establish smooth live playback at that bitrate; buffering or a lower
  media bitrate would be needed when effective delivery is slower.
- There was an earlier interrupted Wi-Fi diagnostic run, retained under
  `artifacts/phone-video-20261024-205004/`. It initially delivered only tens of
  KB/s, then recovered following relay connection replacement. It transferred
  34,465,662 bytes before intentional cancellation and is not counted as a
  complete-file result. The sender recorded 128 logical replays and 571 native
  QUIC loss declarations. The exact initial failure remains unresolved.
- The successful Wi-Fi auto run also replaced its first relay connection before
  file transmission, yielding only four successful idle RTT probes. Its idle
  percentile is weak evidence; the loaded sample is much larger.
- Ordering/deduplication and recovery are in memory. These runs do not prove
  durable resume after process restart or control-session replacement.
- The file copies, private source filename, fingerprints, detailed endpoint
  addresses, logs and JSON/CSV evidence remain in ignored local storage. They
  are not needed in the public repository.

## Controlled direct-path failure

The PC experiment process discarded direct peer UDP for 12 seconds, starting 21.45 seconds after PC start. Router rules and physical interfaces were unchanged. This models receive-path failure, not carrier handover or an actual NAT change.

Receiver path changes (seconds from its own start):

- 2.000: probing (no_authenticated_link)
- 2.406: direct (authenticated_direct_link)
- 24.140: relay (authenticated_relay_link)
- 36.515: direct (authenticated_direct_link)
- 90.562: waiting (peer_unavailable)

The sender and receiver can make path decisions at different times. A final `waiting` event after peer shutdown is teardown, not a transfer outage.

In the controlled failure, the PC selected relay 2.687 seconds after fault
injection and returned to direct 3.062 seconds after reception was restored.
The longest application delivery gap was 3.136 seconds. The sender replayed
256 logical messages; the receiver suppressed 128 duplicate messages. The final
file matched SHA-256. The sender also logged one QUIC error, `Packet contains no
CRYPTO frame`, during direct recovery; connection establishment subsequently
succeeded. That transient handshake error remains a robustness issue to investigate.
These results support improving fixed failure/retry thresholds and cross-link
replay accounting; they do not demonstrate seamless zero-pause switching.

## Cellular forced-relay repeat with recovery snapshots

The user switched back to cellular after the Wi-Fi repeat. Android had no IPv4
Wi-Fi address and the phone's observed public endpoint differed from the PC's.
The same file, coordinator and transport parameters were used. The experiment
added one-second snapshots of existing counting-qlog recovery state on both
endpoints; this adds a small unquantified logging cost, with no protocol changes.

The complete file took 121.27 seconds (3.82 Mbps), compared with 227.12 seconds
(2.04 Mbps) on the first cellular relay run. Its independently computed SHA-256
matched. The receiver's peak one-second goodput was 7.44 Mbps. There were no
connection replacements during file transfer, no logical replays, no duplicate
messages and no delivery gaps longer than one second. Three of 473 loaded probes
did not return; successful loaded probes had median/P95 RTT of 154.1/229.8 ms.

Sender snapshots reveal a congestion-control slowdown inside the run:

| Seconds from phone start, approximate | File submission rate | Sampled QUIC congestion window | Additional QUIC loss declarations |
|---|---:|---:|---:|
| 20-29 | 6.84 Mbps | 970-981 KB | 0 |
| 50-59 | 3.36 Mbps | 11-519 KB | 17 |
| 60-69 | 0.85 Mbps | 4-34 KB | 21 |
| 70-79 | 0.59 Mbps | 9-23 KB | 7 |
| 120-129 | 6.25 Mbps | 133-190 KB | 0 |

Rates above are deltas of file bytes accepted by the sender over approximately
ten-second groups, not receiver timestamps; at most the outstanding logical
window separates submission from delivery. Window ranges come from one-second
samples and may miss shorter extrema. During low-window intervals, sampled
bytes in flight were near the congestion window and the application queue was
mostly full. Thus congestion-window reduction demonstrably constrained this
run, while the earlier high-window phase was limited elsewhere. The sender's
smoothed RTT stayed roughly 110-193 ms across these intervals; RTT growth alone
cannot explain the throughput collapse.

This rules out a stable 2 Mbps ceiling for this cellular relay path. It supports
loss-triggered congestion-window contraction and recovery as a concrete slowdown
mechanism. It does not identify whether loss declarations originated in radio
loss, route congestion, reordering, delayed ACK processing or endpoint scheduling.
No physical packet-loss rate or cellular upload-capacity ceiling was measured.

## Evidence

After the cellular measurements, the user reconnected the phone to Wi-Fi and
requested another forced-relay run. Android Wi-Fi state and the common observed
public endpoint confirmed the switch. With the same video, server, application
window and protocol settings, the repeat delivered 7.37 Mbps, close to the earlier
Wi-Fi forced-relay result of 7.74 Mbps and above the cellular relay run's 2.04 Mbps.
All 245 loaded latency probes returned, although file delivery had one 1.076-second
gap. There were no logical replays or duplicate messages; the file's independently
computed SHA-256 matched. Thus the earlier cellular relay slowdown was not a
fixed 2 Mbps ceiling common to this server/client configuration. These sequential
runs still cannot distinguish cellular upload capacity from RTT, loss/congestion,
route differences or client flow-control effects. A larger-window test and an
independent upload baseline are still needed to locate a throughput ceiling.

- Ignored `artifacts/phone-video-20261024-205205/`: sender/receiver JSON, RTT CSV, progress logs and complete video copies.
- Ignored `artifacts/phone-video-cellular-20261024-214013/`: sender/receiver JSON, RTT CSV, progress logs and complete video copies.
- Ignored `artifacts/phone-video-wifi-repeat-20261024-215503/`: topology snapshot, repeat sender/receiver JSON, RTT CSV, logs and the complete verified video.
- Ignored `artifacts/phone-video-cellular-repeat-20261024-220327/`: topology, complete video, sender/receiver JSON with per-second recovery snapshots, RTT CSV and logs.

Example receiver command (destination must not exist):

```powershell
.venv-win/Scripts/python.exe scripts/file_experiment.py receive --config .local/remote/pc.json --node pc --peer phone --file artifacts/new-copy.mp4 --output artifacts/new-copy-receiver.json --mode auto
```

On the phone, set `PYTHONPATH=~/p2p-overlay-client/client.pyz` and use its
`.venv-e2e/bin/python file_experiment.py send` with the phone config, source path,
`--node phone --peer pc`, and an output JSON path. Both endpoints must run
concurrently. For the controlled fault, add `--fault-after 12 --fault-duration 12`
on the receiver. No runtime transport parameters are overridden.
