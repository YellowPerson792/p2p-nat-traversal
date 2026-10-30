# Real-phone relay comparison, 2026-10-25

This supplements the [direct retest](WIRELESS_PHONE_VIDEO_RETEST.md) with the current
client on actual phone Debian and native Windows. Phone control/data sockets used
the cellular-bound Android bridge; Windows bound to Wi-Fi. Wi-Fi stayed on for
wireless ADB/SSH management. File bytes did not use the management connections.

Both relay trials forced relay-only at both endpoints. Receiver application counters
recorded relay traffic and zero direct messages. Public-IP probes and server TCP
observations independently confirmed cellular and broadband exits.

All four rows used the same 57,952,254-byte video and passed SHA-256 verification.
Goodput includes validated receiver completion and excludes setup/idle probes.
Each row is one sequential observation, not a simultaneous or randomized comparison.

| Direction | Path | Seconds | Mbps | Loaded RTT median / p95 | Max delivery gap | Stalls >1s |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Phone cellular -> PC Wi-Fi | Direct (auto) | 32.75 | 14.16 | 176.3 / 360.7 ms | 412.3 ms | 0 |
| Phone cellular -> PC Wi-Fi | Forced relay | 56.65 | 8.18 | 169.8 / 320.8 ms | 509.0 ms | 0 |
| PC Wi-Fi -> phone cellular | Direct (auto) | 82.99 | 5.59 | 122.7 / 292.0 ms | 288.6 ms | 0 |
| PC Wi-Fi -> phone cellular | Forced relay | 165.46 | 2.80 | 289.5 / 472.2 ms | 297.6 ms | 0 |

RTT is application probe round-trip time, not one-way latency.

| Relay direction | Sender QUIC declared lost / packets sent | Sender / receiver CPU seconds |
| --- | ---: | ---: |
| Phone cellular -> PC Wi-Fi | 162 / 51166 | 47.86 / 18.56 |
| PC Wi-Fi -> phone cellular | 286 / 52222 | 23.58 / 116.86 |

QUIC loss declarations are recovery events, not capture-based physical loss rates.
The runs do not isolate cellular capacity, relay capacity, bridge queues, guest
processing or storage. No transport configuration was changed for this comparison.

The first attempt, `artifacts/phone-video-forward-cellular-20261025-023828/`,
authenticated successfully but failed before file transfer because the old shared
source path had disappeared. It is excluded. The successful upload instead read
the prior received video copy, independently verified to have the same SHA-256.

Evidence:

- `artifacts/phone-video-forward-cellular-20261025-023937/`
- `artifacts/phone-video-reverse-cellular-20261025-024104/`
- `artifacts/wireless-phone-relay-20261025-summary.json`

Temporary bridge processes were stopped. No global routes or server admission
entries were modified. Wireless debugging remains enabled.
