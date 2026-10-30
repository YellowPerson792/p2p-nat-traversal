# Performance experiment summary, 2026-10-25

These are historical custom-file-protocol measurements. For the current port-scoped
proxy, see [two-network proxy validation](../testing/DUAL_NETWORK_PROXY_TEST.md). Its HTTP
latency probes and application transfer method differ; do not combine the tables
as a controlled before/after comparison.

The current evidence shows a direction-dependent throughput gap, substantial
phone-side processing cost, and successful file delivery and path recovery.
It does **not** establish the network capacity or the transport's maximum rate.
This document consolidates retained measurements; no new trials were run for it.

## Master result table

This is the primary comparison table. Every row transfers the same
57,952,254-byte video and has a matching SHA-256. “Loss” is the sender's QUIC
recovery-event count, not a physical packet-loss measurement. CPU is whole
process CPU time, including setup and instrumentation.

| Environment | Direction | Path | Goodput | Loaded RTT median / P95 | Loss events / sent | Sender / receiver CPU | Integrity |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| Two native Windows clients | Cellular → Wi-Fi | Direct | **18.36 Mbps** | 117 / 136 ms | 15 / 50,171 | 5.48 / 5.88 s | pass |
| Two native Windows clients | Wi-Fi → cellular | Direct | **7.75 Mbps** | 111 / 127 ms | 1,178 / 52,387 | 6.69 / 6.95 s | pass |
| Two native Windows clients | Cellular → Wi-Fi | Relay | **11.16 Mbps** | 122 / 146 ms | 335 / 50,970 | 8.03 / 14.25 s | pass |
| Two native Windows clients | Wi-Fi → cellular | Relay | **8.61 Mbps** | 117 / 129 ms | 778 / 51,771 | 8.42 / 14.66 s | pass |
| Real phone Debian + Windows | Cellular → Wi-Fi | Direct | **14.16 Mbps** | 176 / 361 ms | 27 / 50,886 | 28.45 / 7.22 s | pass |
| Real phone Debian + Windows | Wi-Fi → cellular | Direct | **5.59 Mbps** | 123 / 292 ms | 248 / 51,476 | 8.66 / 46.09 s | pass |
| Real phone Debian + Windows | Cellular → Wi-Fi | Relay | **8.18 Mbps** | 170 / 321 ms | 162 / 51,166 | 47.86 / 18.56 s | pass |
| Real phone Debian + Windows | Wi-Fi → cellular | Relay | **2.80 Mbps** | 289 / 472 ms | 286 / 52,222 | 23.58 / 116.86 s | pass |

The two-PC and real-phone rows are separate experiments. The latter includes
the Android cellular bridge and Debian guest; they are not an isolated benchmark
of QUIC versus the bridge.

## Comparable measurements

Each row transferred the same 57,952,254-byte video with matching SHA-256.
Goodput includes receiver completion acknowledgement and excludes setup/idle
probes. Directions name the Internet exit, even when both clients ran on the PC.
Rows were measured sequentially, not under identical radio/load conditions.

| Client environment | Software | Path | Cellular upload to Wi-Fi | Wi-Fi to cellular download |
| --- | --- | --- | ---: | ---: |
| Two native Windows clients; cellular via USB/Android bridge | Before bulk optimization | Direct | 7.57 Mbps | 7.57 Mbps |
| Same two-PC arrangement | After bulk optimization | Direct | **18.36 Mbps** | **7.75 Mbps** |
| Same two-PC arrangement | After bulk optimization | Forced relay | 11.16 Mbps | 8.61 Mbps |
| Actual phone Debian and native Windows; cellular via guest/Android bridge | After bulk optimization | Auto, file delivered direct | **14.16 Mbps** | **5.59 Mbps** |
| Same real-phone arrangement | After bulk optimization | Forced relay | **8.18 Mbps** | **2.80 Mbps** |

The two-PC trials used real broadband/cellular Internet paths, not loopback.
The latest real-phone trial used wireless ADB only for management; video data
crossed the guest/Android cellular bridge. Thus the comparison changes both the
execution environment and the local forwarding path, as well as measurement time.

Sources: [archived two-PC detail](../archive/performance-2026-10-25/DUAL_PC_CELLULAR_VIDEO.md),
[bulk optimization](BULK_TRANSFER_OPTIMIZATION.md), and
[real-phone retest](../testing/WIRELESS_PHONE_VIDEO_RETEST.md), and
[real-phone forced-relay retest](../testing/WIRELESS_PHONE_RELAY_RETEST.md).

## What the observations support

### Cellular download is slower in the latest optimized trials

Upload/download goodput differed by about 2.37 times in the two-PC direct run
and 2.53 times in the real-phone direct run. The asymmetry therefore remains
when Debian and phone shared-storage I/O are removed from the client path.
Those phone components cannot be its sole explanation.

The sender also declared more QUIC packet losses in the download direction:

| Environment / path | Cellular upload: declared lost / packets sent | Cellular download: declared lost / packets sent |
| --- | ---: | ---: |
| Optimized two-PC, direct | 15 / 50,171 | 1,178 / 52,387 |
| Optimized two-PC, relay | 335 / 50,970 | 778 / 51,771 |
| Real-phone retest, direct connection trace | 27 / 50,886 | 248 / 51,476 |
| Real-phone retest, forced relay | 162 / 51,166 | 286 / 52,222 |

These are sender recovery-event counts, not measured physical loss rates or
exact retransmitted file-byte counts. They do not locate loss in the radio,
carrier path, Wi-Fi, Android bridge, guest buffers or scheduling. Reordering can
also cause loss declarations. The real-phone counts exclude its warm relay trace.
Notably, the phone download was slower despite fewer loss declarations than the
two-PC download: loss-event count alone does not explain throughput.

Earlier pre-optimization repeats sometimes showed little directional difference
(3.62 versus 3.35 Mbps). The latest direction gap is a repeated observation in
the optimized trials, not an established permanent property of cellular service.

### The phone environment adds significant processing cost

For the same video in the optimized direct runs, measured endpoint process CPU
times were:

| Role | Native Windows client in two-PC test | Actual Debian phone client |
| --- | ---: | ---: |
| Sender on cellular upload | 5.48 CPU seconds | 28.45 CPU seconds |
| Receiver on cellular download | 6.95 CPU seconds | 46.09 CPU seconds |

The two-PC arrangement delivered about 30% more upload goodput and 39% more
download goodput than the latest real-phone arrangement. Combined with CPU
time, this supports treating the phone execution environment as a performance
concern. It does not prove CPU saturation or assign the difference to Python,
crypto, the VM, shared storage, instrumentation, or the bridge individually.
CPU times cover each whole process, not just the file-transfer interval, and
exclude the separate Android bridge process. These are not controlled hardware
microbenchmarks or measurements of total phone CPU consumption.

### Earlier software restrictions were real, but the remaining ceiling is unknown

The old 128-record window with 891 file bytes per record allowed only 114,048
file bytes outstanding. At about 120 ms RTT, its approximate window/RTT rate
was 7.6 Mbps, close to the old symmetric two-PC result. Full-window samples
supported this diagnosis. Increasing record sizes, correcting clock precision
and avoiding excessive wakeups broke that apparent ceiling.

The current 512 KiB payload budget, larger records and precise QUIC/event-loop
timing do not establish an optimal configuration. Remaining candidates include
congestion control under loss, pacing/burstiness, application ACK timing, socket
queues, bridge forwarding, guest scheduling and synchronous shared-storage I/O.
There is no same-path mature-transport control or repeated saturation curve that
separates those factors. The user's approximately 20 Mbps phone upload speed
test used a different endpoint/protocol; it is context, not the ceiling of this
peer path or evidence about cellular download capacity.

The highest retained **whole-video confirmed goodput** among these runs is
18.36 Mbps. This is an achieved result, not an upper bound; one-second peaks,
local UDP stress results and Internet speed-test rates are different metrics.

## Bridge, relay, latency and robustness

- Earlier no-bridge controls did not remove the low rates: cellular download
  was 2.85 Mbps without the bridge versus 3.35 Mbps with its optimized version;
  upload was 3.74 versus 3.62 Mbps. These were old-software sequential trials,
  not evidence that the bridge has zero cost or is beneficial. Local stress
  tests also demonstrated packet loss without any radio/Internet traversal.
- The relay has no established fixed speed penalty. Optimized two-PC upload
  was slower through relay (11.16 versus 18.36 Mbps), while download was slightly
  faster (8.61 versus 7.75 Mbps). Different routes and changing conditions remain
  confounders; neither server bandwidth nor server processing is universally
  excluded as a bottleneck.
- The later real-phone forced-relay runs reached 8.18 Mbps upload and 2.80 Mbps
  download, below their earlier direct references of 14.16 and 5.59 Mbps.
  Download loaded RTT median/p95 increased from 123/292 to 289/472 ms; the
  phone receiver consumed 116.86 CPU seconds over its 178.32-second whole run.
  This strengthens concern about the real-phone relay path and processing cost,
  but does not isolate the cause. Its 286 sender loss declarations versus 248
  in direct are insufficient by themselves to explain the roughly halved rate.
- In the real-phone retest, idle RTT medians were about 112/120 ms. Loaded
  medians were 176/123 ms and p95 values 361/292 ms for upload/download. The slower
  download therefore did not have the higher median probe RTT. RTT alone does
  not explain the rate difference; probes also do not measure file completion
  latency or one-way delay.
- All four latest phone direct/relay transfers and all four final optimized two-PC fixed-path
  transfers passed file integrity checks with no delivery gap over one second.
  A separate injected eight-second direct-path outage recovered via relay and
  back to direct with a matching file, but caused a 2.78-second delivery stall.
  Recovery works; interruption-free switching is not demonstrated.

## Next measurements needed to locate the ceiling

1. Repeat alternating directions several times on each topology with unchanged
   code, recording radio conditions and background load; report spread rather
   than only the best run.
2. Compare a mature TCP/QUIC transfer on the same hosts and verified exits,
   with the same direct/relay topology where feasible. An unrelated speed-test
   server is insufficient as a peer-path control.
3. Separate transport from storage using a bounded in-memory source/sink, while
   profiling endpoint CPU and collecting guest/bridge socket-drop counters and
   actual queue/buffer limits. Native Android is a useful later control.
4. Inspect time-series congestion window, bytes in flight, RTT, loss declarations
   and application-window occupancy before changing one setting at a time.

Raw evidence remains in ignored `artifacts/`. The main datasets are
`dual-pc-video-20261025-010528/`, `dual-pc-video-20261025-014604/`,
`dual-pc-video-20261025-015040/`, and the two real-phone folders linked in the
[retest report](../testing/WIRELESS_PHONE_VIDEO_RETEST.md). The bridge controls are described
in [the archived bridge audit](../archive/performance-2026-10-25/BRIDGE_PERFORMANCE.md) and
[archived native cellular repeat](../archive/performance-2026-10-25/NATIVE_CELLULAR_FORWARD_REPEAT.md).
