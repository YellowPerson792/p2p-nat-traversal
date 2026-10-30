# Forwarding performance review, 2026-10-30

The proxy keeps the existing P3 framing, approved port/protocol/direction,
512 KiB receive credit and native QUIC buffer limits. These changes reduce
local forwarding overhead; they do not establish a new Internet throughput.

## Changes

| Previous behavior | Change | Bound / latency behavior |
| --- | --- | --- |
| Every reliable record enters a timed coroutine, even with capacity available | Try `Client.send(..., reliable=True, stream=key)` first; wait with the existing 30-second timeout only under backpressure | Uses the same transport admission checks |
| Every received TCP record calls `drain()` and returns a separate CREDIT | Write an available batch and return its combined credit after `drain()` | Stop after approximately 128 KiB; never wait to fill a batch |
| Each UDP packet searches all active channels | Index local UDP channels by `(mapping ID, sender address)` | Remove entries when channels close, expire, reset or their mapping is removed |
| Buffered TCP operations can complete without yielding | Yield after 128 KiB of continuous outbound data, and after each inbound write batch | Cooperative fairness; no strict priority guarantee |

Batching does not return credit for bytes still waiting in the proxy queue.
EOF is emitted after the preceding bytes; TCP half-close remains independent
in each direction. Small requests are processed immediately when available.

Python 3.12's positive-timeout `asyncio.wait_for` already awaits its coroutine
in the current task. Merely replacing it with `asyncio.timeout` is not a task
allocation optimization. The fast path instead avoids creating a timeout for
records accepted immediately.

## Verification

The proxy regression covers transfers larger than the credit window, concurrent
connections under simulated loss, half-close, authorization scope, carrier
switching, UDP sender reuse and index cleanup. A blocked-writer test verifies
that batching preserves byte order, withholds credit until `drain()` succeeds,
returns the exact consumed byte count and propagates EOF.

For 24 queued records of 16,128 bytes, that test groups credit into three records
and uses four drains including EOF, rather than 24 credits and 25 drains.

Run the local dispatch microbenchmark with:

```powershell
.\.venv-win\Scripts\python.exe -m scripts.benchmark_proxy_dispatch
```

On this Windows Python 3.12.10 run, five repetitions of 20,000 accepted records
had median times of 0.078256 s for the previous timed dispatch and 0.016257 s
for the new fast path. This isolates dispatch using an immediately accepting
fake transport. It excludes encryption, sockets, congestion, backpressure and
network latency; it is not an end-to-end speedup claim.

## Remaining limits

- Fixed 512 KiB credit can limit a high-bandwidth/high-RTT TCP channel. Window
  growth must account for both proxy credit and native QUIC admission, total
  memory, and loaded latency; enlarging just one queue is insufficient.
- Shared QUIC congestion control, global send budget and capacity notification
  still couple channels. Cooperative yielding does not implement a scheduler.
- Native sender-buffer occupancy is scanned for aggregate admission; stream
  count and CPU profiling should guide changes to that adapter.
- Python framing, data copies and per-datagram processing remain. No zero-copy
  or line-rate claim is made.
- Direct/relay carriers share RTT and recovery state. That transport choice is
  outside this forwarding-layer change.

The next network experiment should compare a bulk transfer alone, interactive
requests alone, and both concurrently, recording throughput, interactive p95,
CPU, credit wait and QUIC buffer occupancy. Historical measurements in other
documents predate this revision and cannot validate its current network rate.
