# Android cellular bridge performance audit, 2026-10-24

The next [phone-to-PC repeat with the same optimized bridge](CELLULAR_DIRECTION_REPEAT.md)
measured 3.62 Mbps on 2026-10-25, versus 3.35 Mbps in the optimized reverse run.
The much larger earlier direction gap was not reproduced; network conditions
still varied between these sequential observations.

The debug bridge was not fully optimized. It can introduce local packet loss and
scheduling/queueing delay, but measurements do not support a fixed 2-3 Mbps
forwarding ceiling. Removing it did not eliminate the low reverse-file speed.
The working helper was improved and verified with another complete video.

## Code review and changes

The original Java hot path created `DatagramPacket` objects for every receive and
send, repeatedly constructed/formatted addresses, allocated a framed downlink
array and copied the whole UDP payload. Socket buffers used defaults. The Python
adapter also converted the same address headers repeatedly. Neither layer had a
payload retransmission mechanism; QUIC remained responsible for recovery.

The current debug helper now:

- Reuses packets and backing buffers. It receives downlink payload directly after
  the nine-byte local header area, avoiding the previous full-payload copy.
- Caches permitted endpoint addresses and validates them without per-packet text
  conversion. The Python adapter uses bounded 64-entry header/address caches.
- Requests larger, bounded socket buffers and reports the values actually granted.
  Android reports 4,194,304-byte receive budgets on each UDP socket. Debian reports
  458,752 bytes after the request: its `rmem_max` and `wmem_max` are only 229,376.
  No global kernel limits were changed. Reported socket budgets include kernel
  accounting and are not an exact usable-payload queue capacity.
- Keeps the existing server/peer destination restrictions, source address metadata,
  CA/peer-pin verification, HMAC and end-to-end QUIC encryption.

Changing buffer sizes is not a guarantee of lower latency: longer queues can
retain packets while increasing delay. The socket option is a hint and its
actual value must be checked, as described in the
[Android DatagramSocket API](https://developer.android.com/reference/java/net/DatagramSocket#setReceiveBufferSize(int)).

## Local forwarding test, excluding radio and Internet

The test path was Debian -> Android bridge -> Android loopback UDP echo -> bridge
-> Debian. The bridge's socket was cellular-bound, but the echo destination was
127.0.0.1, so benchmark payload did not traverse a base station or public server.
Packets had 1,200 raw payload bytes plus the local address header. Each rate was
offered for about three seconds, followed by one second of drain. Burst cases
sent five batches with 200 ms spacing. RTT includes the guest test program,
virtual-network scheduling, both bridge directions and the native echo program.
These are single short stress trials, not stable capacity measurements.

| Configuration | Offered rate | Delivered echo rate | Echoes not returned | RTT P95 |
|---|---:|---:|---:|---:|
| Original | 9.99 Mbps | 9.99 Mbps | 0 / 3,123 | 29.5 ms |
| Original | 24.95 Mbps | 23.25 Mbps | 534 / 7,812 | 36.7 ms |
| Original | 49.98 Mbps | 46.81 Mbps | 994 / 15,625 | 22.0 ms |
| Optimized Android, original guest buffer | 24.96 Mbps | 24.29 Mbps | 208 / 7,812 | 30.9 ms |
| Optimized Android, original guest buffer | 49.90 Mbps | 47.40 Mbps | 782 / 15,625 | 17.0 ms |
| Optimized Android and guest buffer request | 24.96 Mbps | 24.20 Mbps | 238 / 7,812 | 45.7 ms |
| Optimized Android and guest buffer request | 49.98 Mbps | 45.25 Mbps | 1,480 / 15,625 | 48.2 ms |

The original 128-packet bursts returned all 640 echoes but had P95 RTT of
111 ms. Original 512-packet bursts lost 1,594 of 2,560 echoes. The Android-only
optimization reduced that count to 386; with the larger guest buffer request,
705 did not return. Thus the larger guest request did not consistently improve
the observed end-to-end result; scheduling and load variation remain significant.
The 512-packet stress exceeds the normal application's 128-message window.

Counters provide additional localization. Original Java upload/download counters
each covered 28,298 of 29,760 submitted packets; the guest received 26,638. Both
optimized trials counted all 29,762 submitted packets at Java upload and download,
while the guest received 28,386 and 27,339 respectively. Successful UDP send calls
are not delivery guarantees. Remaining losses lie after the counted Java send
and before the guest callback; these counters alone cannot assign each loss to
one kernel queue. The test does establish that local loss is possible without
any cellular-radio or Internet loss.

## Full-file cellular direct comparison

All cases sent the same 57,952,254-byte video from Windows to the phone using
`direct-only`, the unchanged 128-message window, and the existing congestion
controller. All three complete copies independently matched SHA-256 on the phone.

| Route | Confirmed time | File goodput | Loaded RTT median / P95 | Max delivery gap |
|---|---:|---:|---:|---:|
| Original bridge | 183.99 s | 2.52 Mbps | 157.8 / 297.5 ms | 1,265.5 ms |
| Native VM cellular path, no bridge | 162.58 s | 2.85 Mbps | 147.6 / 237.8 ms | 211.2 ms |
| Optimized bridge | 138.51 s | 3.35 Mbps | 191.5 / 337.2 ms | 486.5 ms |

The no-bridge control briefly disabled phone Wi-Fi so Android's virtual tethering
upstream selected cellular naturally. The original client ran in Debian without
the Python route adapter or Android UDP helper. Wi-Fi and wireless ADB were
restored afterward. For bridge cases, Wi-Fi stayed on for management and the
helper explicitly bound control/data sockets to cellular. Server endpoint
observations verified the cellular exits and all receiver application messages
were direct, with zero relayed messages. The optimized run had no duplicate
messages, invalid packets or logical replays; it recorded 188 sender QUIC loss
declarations versus 245 with the original bridge and 264 without it.

These sequential runs do not isolate an exact optimization benefit. The carrier
public exit changed between the original and later tests, and disabling Wi-Fi
also moves other phone traffic onto cellular. The optimized bridge being faster
than the no-bridge run is itself a warning against interpreting the differences
as a fixed per-layer cost. The defensible conclusion is that local forwarding
has avoidable costs, but removing it is insufficient to solve the bulk-transfer
limit. QUIC/application windows, ACK processing, guest/host scheduling and the
actual path's loss/latency remain relevant. A lower UDP loss declaration count
does not establish a physical packet-loss rate.

## Better approaches

- For a clean benchmark, native Debian cellular forwarding with Wi-Fi disabled
  removes the debug bridge from the data path. It sacrifices the Wi-Fi management
  link during the test and requires a verified independent restoration method.
- For a client that must keep Wi-Fi while selecting cellular, running the client
  on Android and binding its own sockets avoids both the VM crossing and this
  extra proxy. Android provides
  [Network.bindSocket](https://developer.android.com/reference/android/net/Network#bindSocket(java.net.DatagramSocket));
  an Android-native runtime still needs packaging and network-lifecycle support.
- Keeping Debian and directly assigning its virtual tethering upstream would
  avoid per-packet userspace forwarding. No working per-VM shell selector has
  been verified on this phone. Binding the Terminal application's process does
  not bind forwarded guest packets. Current-family AOSP tethering uses automatic
  upstream selection, so an old resource preference flag is not a demonstrated
  solution: see [TetheringConfiguration](https://android.googlesource.com/platform/packages/modules/Connectivity/+/c62bfff71b95089d9d1b6e4cac96da572f2fe9da/Tethering/src/com/android/networkstack/tethering/TetheringConfiguration.java).

The helper remains debug tooling, not a production backend: it assumes one guest,
IPv4, a selected active cellular network and a small destination allowlist. It
does not yet implement complete network-loss/rebinding lifecycle management,
batch UDP syscalls or detailed CPU/queue-drop profiling. More buffers or switching
Java APIs should not be presented as a guaranteed fix without measurements.

## Diagnostic issues and cleanup

Two local attempts timed out before sending benchmark data after a new diagnostic
read socket options *after* starting blocking receive threads. Those option reads
were moved before thread startup; the final local and full-file runs passed.
These attempts are retained but excluded from performance results.

The no-bridge Wi-Fi recovery watchdog also failed. A read-only reproduction
showed Android `cmd wifi status` failed with a Binder transaction error when its
output FD was an external-storage log file, but succeeded through a pipe into
the same file. The watchdog now captures command output through a pipe, retries
and verifies `Wifi is enabled` before claiming success. Its detached, already-on
verification passed; a complete disable/restore cycle has not been re-tested.
The user manually restored connectivity for the failed control run. No additional
Wi-Fi disruption was used for the final optimized test.

All benchmark/bridge processes have stopped. Wi-Fi, wireless ADB and persistent
Debian SSH are working. Original helpers and measurements are preserved as ignored
evidence; no old bridge is added as a maintained selectable project transport.

## Evidence and working files

- `artifacts/bridge-audit-20261024/`: local baseline/optimized JSON and logs,
  the no-bridge full-file control, recovery diagnostics and source snapshots.
- `artifacts/phone-video-reverse-cellular-direct-20261024-232440/`: original
  bridge full-file baseline.
- `artifacts/phone-video-reverse-cellular-direct-20261024-235119/`: optimized
  bridge full-file JSON, RTT CSV, logs, hashes and helper snapshots.
- `.local/android-cellular/CellularBridge.java` and `phone_via_cellular.py`:
  current experimental implementation. The updated jar and adapter are deployed
  on the phone; the bridge is started only for an experiment and then stopped.
