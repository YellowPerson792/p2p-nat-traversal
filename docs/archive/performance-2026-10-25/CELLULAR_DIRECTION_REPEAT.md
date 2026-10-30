# Cellular direction repeat, 2026-10-25

A subsequent [USB-managed repeat with Wi-Fi disabled and no bridge](NATIVE_CELLULAR_FORWARD_REPEAT.md)
delivered **3.74 Mbps in 123.91 seconds**, compared with this optimized-bridge
run's 3.62 Mbps. The old 6.50 Mbps rate was not reproduced without the bridge either.

The same 57,952,254-byte video was sent from the phone back to the PC using the
current optimized Android cellular bridge and `direct-only`. Wi-Fi remained on
for wireless ADB/SSH management. The earlier reverse optimized run used the same
helper implementation, 128-message application window, QUIC settings and file
experiment. Neither run permitted relay fallback.

Both source/destination files independently matched SHA-256. The phone source
was read from Android shared Download storage; the PC receiver wrote to native
Windows storage. In the reverse run the phone wrote to shared Download storage.
The source was not re-encoded and no video payload was transferred via ADB/SSH.

| Measurement | Phone to PC, repeat | PC to phone, optimized reference |
|---|---:|---:|
| Confirmed complete-file seconds | 128.08 | 138.51 |
| Effective file Mbps | 3.62 | 3.35 |
| Loaded RTT median, ms | 155.75 | 191.48 |
| Loaded RTT P95, ms | 211.74 | 337.21 |
| Max delivery gap, ms | 324.75 | 486.48 |
| Receiver peak one-second Mbps | 6.36 | 5.16 |

Both runs had zero relayed application messages, duplicate messages and logical
replays, and no file delivery gaps over one second. The repeat lost 2 of 504
loaded unreliable RTT probes; successful probes alone enter the latency statistics.
Its sender declared 190 QUIC packet losses, versus 188 in the reverse reference.
These declarations are not a capture-based physical loss measurement.

Phone-to-PC goodput was **8.1% higher** in
these observations. The earlier 6.50 Mbps phone-to-PC result was not reproduced
under the current software/bridge setup. Consequently the earlier large
forward/reverse gap cannot be assumed to be a stable directional property.

This is still a sequential single-run comparison: the phone's cellular private
address, public egress and active network ID changed before the repeat. Signal,
carrier routing and concurrent traffic were not held constant. The equal helper
implementation improves the software comparison but does not make the network
conditions identical. It cannot isolate storage, operating-system processing or
ACK behavior by itself.

The phone's socket buffers were granted 458,752 bytes; Android bridge receive
budgets were 4,194,304 bytes. The receiver returned 38,713 UDP
packets totaling 3,276,203 UDP payload bytes over the whole
process. These include confirmation/probe/control traffic and exclude IP/UDP
headers. They should not be called a pure ACK count or a physical bandwidth total.

Confirmed file timing includes receiver flush/fsync and its validated completion
reply, and excludes setup, idle probes and source hashing. RTT includes endpoint
scheduling and both directions; it is not one-way delay. Mbps uses decimal units.

Ignored evidence:

- `artifacts/phone-video-forward-cellular-direct-20261025-000558/`: PC received video, sender/receiver JSON, RTT CSV, logs,
  bridge counters, endpoint verification and independent hashes.
- `artifacts/phone-video-reverse-cellular-direct-20261024-235119/`: optimized reverse reference.

The bridge was stopped after the run; Wi-Fi, wireless ADB and SSH remained
available. No transport setting or global network route was changed for this repeat.
