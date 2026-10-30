# Interactive SSH and Loaded Transfer Test (2026-10-30)

This record measures an actual interactive SSH session over the overlay, then
repeats the measurement while a large file is transferred on a separate SSH
connection. It complements the SCP throughput benchmark; it is not a packet
loss or one-way-delay measurement.

## Setup

- Client A used Windows Wi-Fi interface 25 (`192.168.5.4`).
- Client B used the USB tether interface (`10.253.162.45`) to send UDP through
  the Android cellular network (`rmnet16`). The phone's default network stayed
  on Wi-Fi, so the bridge explicitly selected cellular for overlay traffic.
- The target was a temporary OpenSSH server and interactive `bash` PTY in WSL.
- The SSH connection was persistent. The test sent a real character (`x`) and
  waited for the remote PTY echo, then sent a command and waited for its prompt.
- The loaded transfer used a second SFTP connection and therefore a separate
  QUIC stream within the same approved overlay connection.
- Direct and relay modes each used one approved TCP forwarding connection.

## Results

`key echo` is the time from writing one character to receiving its remote PTY
echo. `command result` includes command execution and prompt delivery. These
are application response times, not a single-packet RTT.

| Case | Samples | Key echo median / P95 (ms) | Command median / P95 (ms) | Concurrent file goodput |
| --- | ---: | ---: | ---: | ---: |
| Local Windows -> WSL baseline | 20 | 43.8 / 46.7 | 43.8 / 47.5 | — |
| Direct, idle | 30 | 139.5 / 179.1 | 148.3 / 164.0 | — |
| Direct, Wi-Fi -> cellular | 70 | 142.0 / 164.0 | 143.6 / 169.0 | 8.03 Mbps |
| Direct, cellular -> Wi-Fi | 37 | 196.1 / 240.7 | 196.2 / 248.1 | 19.93 Mbps |
| Relay, idle | 30 | 159.6 / 175.3 | 155.3 / 167.0 | — |
| Relay, Wi-Fi -> cellular | 70 | 144.1 / 170.5 | 146.7 / 170.2 | 5.36 Mbps |
| Relay, cellular -> Wi-Fi | 70 | 141.1 / 162.7 | 125.8 / 174.6 | 8.94 Mbps |

All four transferred files matched the source SHA-256. The direction labels are
from the file sender's point of view: Wi-Fi -> cellular is the cellular download
case, while cellular -> Wi-Fi is the cellular upload case.

## Interpretation

The measured interactive delay is usable for a shell but clearly noticeable:
roughly 140--160 ms when idle, with a higher 196 ms median in one direct
cellular-upload sample. Relay adds about 20 ms to the idle key median in this
run, but loaded values overlap the direct values. The relay download run had a
single 434 ms key-echo maximum; its P95 was 170 ms, so this is a tail event and
not the typical delay.

The concurrent file stream did not prevent the control stream from making
progress. QUIC stream separation avoids waiting for missing bytes from the file
stream at the application stream boundary, but streams still share the carrier,
UDP path, congestion state and endpoint CPU. The measured goodput was 5.36--
19.93 Mbps; this run does not establish a stable radio capacity or prove that
the overlay is the limiting factor. The earlier SCP run had different timing
and radio conditions, so its values should not be averaged with these results.

SSH authentication took about 861--979 ms and PTY startup about 322--435 ms in
the network cases. Those are connection setup costs and should be separated
from keystroke latency for a persistent SSH session. The local 43.8 ms baseline
includes Windows-to-WSL handling; no fixed amount is subtracted from the
network measurements because a clean direct-IP versus localhost cross-check was
not completed.

The overlay counters showed no invalid packets, unexpected sources, UDP socket
errors or injected peer drops. They do not expose all native QUIC loss and
retransmission events, so they cannot be converted into a physical packet-loss
percentage. No forced carrier failure or automatic direct/relay migration was
injected in this test.

## Recommended follow-up matrix

1. Run a timestamped UDP sequence echo for p50/p95/p99 delay, jitter and
   timeout-based loss; use synchronized clocks only for one-way delay.
2. Run persistent TCP request/response and HTTP keep-alive small-object tests,
   separating first-connect time from application response time.
3. Run `iperf3` TCP forward/reverse with one and four parallel flows for 60--120
   seconds, with warm-up and JSON output. UDP tests need an explicitly approved
   UDP forwarding port.
4. Repeat SSH PTY, SFTP large files and many-small-files tests in randomized
   order, three to five times per mode and direction.
5. Inject direct-path failure, relay-path failure, idle NAT expiry and phone
   screen-off conditions; record reconnect time, stream continuity and hashes.
6. Sample native endpoint CPU, memory, event-loop lag and queue depth. A zero
   CPU value from the earlier sampler is invalid and is intentionally omitted.

Raw samples and hashes remain in the ignored directory
`artifacts/scp-dual-20261030-201838/`; the reusable drivers are in the ignored
`.local/scp-bench/` directory.
