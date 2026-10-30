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
