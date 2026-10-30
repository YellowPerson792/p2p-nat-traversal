# QUIC transport comparison — 24 October 2026

The new transport uses two independently authenticated end-to-end QUIC connections
in auto mode: direct plus a warm relayed backup. Reliable streams are the default;
unacknowledged logical messages survive path selection and are deduplicated.
The old Noise transport is no longer a runtime option.

## Small controlled comparison

One native Windows process hosted the TLS coordinator and two clients on loopback.
Both versions sent 256-byte messages at 20 messages/second, with a 3-second drain.
Ordinary scenarios sent for 3 seconds; the switch scenario sent for 10 seconds.
The baseline source was commit `d2a74b3`, executed in the same Python 3.12
environment (cryptography 50.0.2; noiseprotocol 0.3.1 for baseline only).
Results are single short runs, not capacity measurements or real-NAT success rates.

| Scenario | Noise echoes / submitted | QUIC echoes / submitted | Noise mean RTT | QUIC mean RTT |
| --- | ---: | ---: | ---: | ---: |
| Direct, no injected loss | 60 / 60 | 60 / 60 | 0.38 ms | 0.65 ms |
| Relay only | 60 / 60 | 60 / 60 | 1.30 ms | 1.05 ms |
| Drop every tenth outbound client UDP packet | 50 / 60 | 60 / 60 | 0.37 ms | 24.42 ms |
| Block direct reception at seconds 2–6 | 141 / 200 | 200 / 200 | 0.55 ms | 387.40 ms |

Loss injection covers all client UDP traffic, including acknowledgements and
registration; it does not select an identical set of application messages across
protocols. The switch run has both clients discard direct packets while leaving
server traffic working. Noise RTT averages include only surviving echoes;
QUIC additionally measures recovered messages. QUIC's switch p95 was about
2.30 seconds: reliability recovered data, not time. No message loss was observed
within the tested windows; this does not guarantee delivery during arbitrary outages.

## Observed cost

Counts below cover the clients' UDP send attempts after setup, including the drain,
retransmissions, ACKs, registration and backup keepalives. They exclude WSS, server
egress, UDP/IP headers and Ethernet headers, so they are not total wire bandwidth.

| Scenario | Noise packets / UDP payload bytes | QUIC packets / UDP payload bytes | Noise / QUIC process CPU seconds |
| --- | ---: | ---: | ---: |
| Direct | 150 / 60,402 | 411 / 60,356 | 0.016 / 0.125 |
| Relay | 126 / 77,618 | 384 / 78,610 | 0.063 / 0.500 |
| Injected loss | 141 / 57,800 | 416 / 64,965 | 0.047 / 0.203 |
| Switching | 397 / 175,548 | 1,098 / 228,641 | 0.156 / 2.047 |

QUIC sent more packets and consumed more CPU in these runs. Compact binary
envelopes offset much of the payload-size overhead, but extra packets also add
unmeasured UDP/IP overhead. Recovery delivered more data in the loss/switch cases.
CPU figures include both clients and the server in one process, exclude setup,
and are noisy at this duration. They do not establish a general performance win.

## Two-NAT namespace validation

The existing isolated Linux/WSL lab was also run with real processes and packet
captures. This validates routing through simulated NATs, not physical routers.

| Scenario | Echoes / submitted | Relay packets |
| --- | ---: | ---: |
| Direct-only | 160 / 160 | 0 |
| Direct blocked from start | 160 / 160 | 1,022 |
| Failure and recovery | 640 / 640 | 1,828 |
| Peer process restart | 236 / 236 | Not compared |

The restart benchmark scheduled 360 sends; 124 opportunities without a ready path
were not submitted. This is not restart-safe persistence. Relay counters include
QUIC handshake/ACK/keepalive packets, including warm backup traffic; they are not
application-message counts. Lab resources and forwarding state were cleaned up.

An initial lab run exposed a filesystem bottleneck: 200 registry `stat` calls on
the Windows-mounted project took 1.37 seconds. Per-packet permission checks then
delayed relay processing and left messages outside the observation window. The
server now caches data-plane policy reads for 100ms, while admission and identity
binding force fresh checks. The table above records the successful run after
that change. Revocation has this additional cache interval, not instantaneous
packet-level visibility.

A Windows address-change test also found that ICMP Port Unreachable could stop
Python 3.12 Proactor UDP reception. Shared client/server socket setup now disables
that reporting using [SIO_UDP_CONNRESET](https://learn.microsoft.com/en-us/windows/win32/winsock/winsock-ioctls#sio_udp_connreset).
Regression tests probe a closed port, then verify reception, path recovery and
control-session/certificate renewal. This is a platform robustness fix, not a
claim that QUIC alone handles all address changes.

## Reproduce and evidence

```powershell
.venv-win\Scripts\python.exe scripts/transport_experiment.py --label quic --output artifacts/quic-comparison/quic.json
# Optional: --delivery datagram
wsl -d Ubuntu-24.04 -u root -- .venv/bin/python scripts/experiment.py
```

Local raw comparison JSON: `artifacts/quic-comparison/noise.json` and `quic.json`.
Final namespace evidence: `artifacts/20261024-180215/`. The earlier diagnostic run
is retained in `artifacts/20261024-175941/`. These ignored artifacts contain the
detailed records; only this summary is tracked. The expanded baseline source was
moved to the ignored local archive; it is not shipped or imported by the application.

Remote server and phone deployment have not been performed. Current limits and
trust assumptions are described in [ENCRYPTION.md](../guides/ENCRYPTION.md).
