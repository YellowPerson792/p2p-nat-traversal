# SCP dual-network benchmark, 2026-10-30

The current release was deployed to the coordinator as `20261030-181145`.
The coordinator health check passed, and the deployed hashes of `overlay/client.py`,
`overlay/server.py` and `overlay/secure.py` matched the local source hashes.
The full local regression suite passed **106 tests** before the network run.

Two native Windows clients ran on the same computer:

- Wi-Fi client: bound to the computer's Wi-Fi interface and broadband exit.
- Cellular client: project UDP bridge over USB to the Android phone's `rmnet16`,
  with a separately observed cellular public exit.
- The phone stayed on Wi-Fi for management; its cellular-bound bridge sockets
  were selected explicitly with Android `Network.bindSocket()`.
- The coordinator observed both control connections from the expected exits.

Each trial created a fresh approved TCP port instance and a parallel UDP echo
port instance. The TCP application was OpenSSH `scp -O` using a temporary
AsyncSSH SCP server on the receiving side. The same 57,952,254-byte video was
sent in every row and the receiver compared its SHA-256 with the source.
Goodput includes SSH connection setup and the SCP completion acknowledgement;
the subsequent local fsync and hash check are outside the timed interval.
The UDP echo was only a concurrent path/latency probe on its own approved QUIC
connection; it does not measure competing streams on the SCP connection.
Its timeout count is not
a direct physical packet-loss measurement.

| Mode | Direction | Time (s) | Goodput (Mbps) | UDP probe RTT median / P95 (ms) | UDP sent / received | SHA-256 |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| Direct only | Wi-Fi -> cellular | 56.47 | **8.21** | 92.4 / 102.9 | 197 / 193 | match |
| Direct only | Cellular -> Wi-Fi | 33.23 | **13.95** | 97.0 / 117.1 | 128 / 128 | match |
| Relay only | Wi-Fi -> cellular | 66.35 | **6.99** | 99.9 / 111.8 | 235 / 231 | match |
| Relay only | Cellular -> Wi-Fi | 43.67 | **10.62** | 105.0 / 131.5 | 163 / 162 | match |

The direct rows recorded zero relayed application messages, and the relay rows
used the coordinator allocation as requested. The relay penalty in this run was
about 15% for Wi-Fi to cellular and 24% for cellular to Wi-Fi, but this is one
sequential sample and is not a general capacity estimate. The direction gap is
also consistent with the cellular path being asymmetric. A controlled capacity
study would need repeated randomized order, longer runs and independent link
capacity measurements.

Temporary approval entries were removed, and clients, the temporary SSH server
and the cellular bridge stopped after the run. Test credentials remain in ignored
local evidence. The original remote member registry was verified
unchanged. Raw evidence is retained in the ignored directory
`artifacts/scp-dual-20261030-182041/`, including `report.json`, client logs,
per-direction received files and the source hash manifest.

## Independent repeat after restoring Windows tool access

The repeat used the same video and Windows OpenSSH `scp -O`, with temporary
Ed25519 user authentication and pinned SSH host keys. Both overlay daemons and
the SSH/SCP target ran natively on the PC. Wi-Fi traffic was bound to interface
25; the other daemon used a USB UDP bridge explicitly bound to Android cellular
network 169 (`rmnet16`). The coordinator observed distinct broadband and cellular
endpoints. The phone's default network remained Wi-Fi (168).

One approved TCP connection was created for each mode and reused for the two
directions. Each transfer opened a new local TCP connection/native QUIC stream.
A separate persistent SSH connection on the same approved port issued short
`probe` commands before and during SCP. The source video size and SHA-256 matched
all four receivers.

| Mode | File direction | Time (s) | Goodput (Mbps) | Idle SSH command median (ms) | Loaded SSH command median / P95 (ms) |
| --- | --- | ---: | ---: | ---: | ---: |
| Direct only | Cellular -> Wi-Fi | 43.65 | **10.62** | 205.3 | 217.7 / 260.3 |
| Direct only | Wi-Fi -> cellular | 56.81 | **8.16** | 199.8 | 206.0 / 228.1 |
| Relay only | Cellular -> Wi-Fi | 47.22 | **9.82** | 210.9 | 236.8 / 281.0 |
| Relay only | Wi-Fi -> cellular | 62.15 | **7.46** | 214.4 | 223.2 / 249.6 |

SSH command timing includes channel creation, command execution and response
delivery, which involve multiple protocol exchanges. These are application
response times, not one-packet RTT, and cannot be directly compared with the UDP
echo column above. The loaded probes use a separate QUIC stream within the same
QUIC connection as the file transfer. They share congestion and CPU resources
without waiting for missing file-stream bytes to be delivered in order.

Goodput includes SSH setup and SCP completion, excludes the later hash check,
and does not imply receiver fsync. The driver polled process completion every
0.5 seconds, adding up to roughly 0.5 seconds to each measured duration. The
local loopback SCP baseline completed by the first poll (reported as 0.51 seconds,
about 906 Mbps). That coarse baseline checks that the SSH target can substantially
exceed the measured Internet rates; it is not a precise local throughput limit.
Native CPU sampling returned no usable values, so no CPU result is claimed.

In this repeat, relay goodput was approximately 8% lower for cellular upload and
9% lower for cellular download. Cellular download remained slower in both modes.
The earlier sequential run above had a larger relay penalty and faster cellular
upload. Neither run establishes a stable capacity limit or isolates a software
regression; radio conditions, routing and test order were not controlled.

Direct trials recorded zero relayed application records; relay trials recorded
zero direct application records during transfer. Both endpoints reported zero
invalid packets, unexpected-source packets, injected peer packet drops, UDP
socket errors and failed pending data. These counters do not expose native QUIC
loss/retransmission and cannot establish a physical packet-loss percentage.
No mid-transfer carrier failure or automatic-mode migration was injected.

Post-run comparison verified that all 27 installed Python/static files matched
local hashes on coordinator release `20261030-181145`. Original members were
preserved, the two test members were revoked and removed with exact public-key
checks, and a registry backup was retained. Clients, SCP target and cellular
bridge were stopped; the deployed coordinator remains running. Raw evidence,
source hashes and received files are retained under ignored
`artifacts/scp-dual-20261030-195103/`. Preparation-only attempts at 19:46--19:50
are excluded. The repeat drivers and temporary dependencies are under ignored
`.local/scp-bench/` and `.local/scp-tools/`.
