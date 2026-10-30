# Bulk-transfer optimization, 2026-10-25

The previous file sender was limited by a 128-record application ACK window,
891-byte file chunks, and coarse Windows timing. This change removes those
artificial restrictions while retaining bounded replay across direct/relay
connections. It does not claim to have reached the network's absolute capacity.

## Final implementation

- Reliable records allow **16 KiB** of application payload; the file experiment
  puts **16,375 file bytes** in each record, leaving nine bytes for type/offset.
  QUIC still segments the stream into its existing **1200-byte QUIC packets**.
  Unreliable QUIC datagrams remain limited to 900 application bytes.
- The send and reordering windows are bounded by **512 records and 512 KiB of
  application payload**, rather than growing without limit with file size.
  QUIC receive flow-control credit is 1 MiB per stream and 2 MiB per connection.
- `send_reliable()` waits for capacity through an asyncio event. Cumulative
  ACKs wake blocked senders only when capacity is actually released. Path
  readiness changes also wake them; peer reset/closure ends a blocked send.
- Normal sends append only the new record. A full replay scan occurs on a
  different connection token. Cumulative ACK cleanup removes only the ordered
  acknowledged prefix. Application ACKs remain necessary for deduplication and
  replay between the two independent QUIC connections.
- QUIC timestamps use `perf_counter()` consistently. On the tested Windows
  Python 3.12, `monotonic()` uses `GetTickCount64` at **15.625 ms** resolution;
  `perf_counter()` uses `QueryPerformanceCounter` at **0.1 microsecond** resolution.
  The client event loop uses the latter clock/resolution on Windows versions
  that expose a coarse monotonic clock. Other platforms keep their usual loop.
- Maintenance sleeps until the earlier of 10 ms or QUIC's next timer deadline,
  with a 1 ms minimum wait. This services pacing without an unbounded spin.

TLS ALPN is **`overlay-quic-v2`**. Both endpoints need the updated client; old
clients fail ALPN negotiation rather than misparsing larger records. The signed
identity-binding suite and coordinator protocol are unchanged, so the existing
coordinator/relay was used without upgrading it. The phone bundle builder now
includes `overlay/runtime.py`; rebuild the bundle before using an old phone VM
client with the updated PC client.

## Why increasing the message count alone was insufficient

Exploratory runs with 512 and 2048 small records reached roughly 8–9.5 Mbps.
Increasing the count beyond 512 did not produce a consistent improvement, so
the final code does not retain the 2048-record setting or separate legacy mode.

A 58 MB transfer profile recorded **618,633 maintenance-loop resumptions in
58.6 seconds**, rather than the roughly 100 per second intended by its 10 ms
sleep. asyncio's coarse-clock early-callback allowance repeatedly woke both
maintenance and capacity waiters. Suppressing redundant wakeups and correcting
the event-loop clock reduced sender CPU time from roughly 48 seconds to 5
seconds on the same-size trial, before increasing throughput.

QUIC also received coarse `monotonic()` timestamps. Its pacing bucket could
only refill in 15.625 ms steps, producing batches despite finer event-loop
timing. Changing all peer-link timestamps consistently to the high-resolution
clock removed this second limit. Both fixes matter: altering only the loop
clock reduced CPU consumption but left throughput around 9.5 Mbps.

## Full-video comparison

Two native Windows clients ran on the same PC. One bound to Wi-Fi; the other
used UDP over USB networking to the phone's cellular-bound UDP bridge. Android
stayed on Wi-Fi for management. The two server-observed public IP addresses and
control connections confirmed independent broadband and cellular exits.

Each run transferred the same **57,952,254-byte** video. Every receiver matched
SHA-256 `a1db251abf31d98ee2211695b800216e613efcfe77593f13a4f52405db75cc5e`.
Goodput includes receiver completion confirmation but excludes connection setup
and idle probes. Tests ran sequentially, one transfer per direction/path.

| Path | Direction | Before | After | After transfer time | Loaded RTT mean / p95 |
| --- | --- | ---: | ---: | ---: | ---: |
| Direct | Cellular → Wi-Fi | 7.57 Mbps | **18.36 Mbps** | 25.25 s | 119.4 / 136.1 ms |
| Direct | Wi-Fi → cellular | 7.57 Mbps | **7.75 Mbps** | 59.79 s | 112.9 / 126.8 ms |
| Forced relay | Cellular → Wi-Fi | 7.06 Mbps | **11.16 Mbps** | 41.54 s | 124.5 / 146.0 ms |
| Forced relay | Wi-Fi → cellular | 6.49 Mbps | **8.61 Mbps** | 53.85 s | 117.4 / 129.0 ms |

All four final runs had **zero delivery stalls over one second**. Maximum
application file-delivery gaps were 194, 313, 205 and 244 ms respectively.
The sender's whole-run CPU times were 5.48, 6.69, 8.03 and 8.42 seconds.
Loaded RTT did not increase systematically: the before values were approximately
115.8, 113.5, 126.1 and 123.2 ms respectively. These samples do not establish a
universal latency reduction; no growing application file queue was introduced.

Sender QUIC recovery traces declared 15, 1178, 335 and 778 lost packets out of
approximately 50–52 thousand sent packets. These are protocol loss declarations,
not direct physical loss measurements. The cellular-download runs still suffer
significantly more loss and show a smaller gain. The next investigation should
separate path loss, burst delivery/socket buffering and QUIC congestion control;
simply enlarging the application window further is not justified by these runs.
The earlier generic SSH speed test uses a different transport and destination,
so it is a reference, not proof of the exact capacity of these peer paths.

## Evidence and limits

- Before: `artifacts/dual-pc-video-20261025-010528/`.
- Final four runs: `artifacts/dual-pc-video-20261025-014604/`.
- CPU profile: `artifacts/dual-pc-video-20261025-013915/`.
- Event-loop fix before QUIC clock fix: `artifacts/dual-pc-video-20261025-014126/`.
- The deliberately stopped intermediate run `...-014254/` is incomplete and is
  not included in the results table.

Intermediate measurements and received files are retained as ignored evidence;
there are no production switches retaining old transfer implementations. The
USB Android bridge remained the same for the comparison. Temporary test
identities were removed and the original approval registry verified restored
after each run. No system-wide routes, firewalls or core server deployment were
changed.

## Recovery and automated checks

A separate auto-mode transfer (`artifacts/dual-pc-video-20261025-015040/`) dropped
received direct UDP for eight seconds, starting five seconds after the first
file bytes. Both clients changed from direct to relay and later back to direct.
The entire video still matched SHA-256. Confirmed goodput including the fault
was 8.74 Mbps, with one 2.78-second delivery stall. The ordinary fixed-path runs
had no such injected fault. Failure detection and re-establishment latency are
still limitations; this change preserves recovery rather than redesigning path
selection. Application replay on the sender and duplicate suppression on the
receiver remain in effect.

The automated suite passed **43 tests**, including large reliable records under
packet loss, bounded byte-window backpressure, release by cumulative ACK,
blocked-send termination after peer reset, short-sleep timing and phone zipapp
packaging. UDP datagram sizes remained within the existing envelope limit in
the large-record recovery test. Both the real fault trial and final fixed-path
trials restored the original registry and stopped the helper afterward.

## Remaining scope

The receiver still writes to disk synchronously and acknowledges completion
after `fsync`; this is not a persistent resumable-transfer protocol. Automatic
BDP tuning, ACK batching, alternate congestion controllers and socket/bridge
tuning are left for later investigation, once this application baseline is
reviewed.
