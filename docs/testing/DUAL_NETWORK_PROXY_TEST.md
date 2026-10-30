# Two-network proxy test, 2026-10-25

Two native Windows client daemons ran on the same PC. One bound its directory,
data-control and UDP sockets to Wi-Fi 192.0.2.11 / interface 26. The other used
USB networking (192.0.2.8 -> 192.0.2.9) to an Android helper whose TCP/UDP
sockets were explicitly bound to cellular network 115, while Android default
network 114 remained Wi-Fi. No global routes were changed.

The coordinator observed Wi-Fi at 198.51.100.5 and cellular at 198.51.100.4.
Independent public-IP probes and server TCP observations supported the exits.
Cellular probe egress changed between setup attempts; the test therefore
validates distinct actual endpoints instead of hardcoding an earlier carrier IP.

The server was upgraded from release 20261024-184201 to **20261025-221134**.
The prior release and /etc registry snapshot were retained. The live registry
now uses /var/lib/p2p-overlay/members.json, supporting browser approvals.

The clients generated two isolated temporary identities through their HTTP APIs.
Both waited for administrator approval, then appeared in the directory.
For every trial, the requester named the recipient application port and TCP/UDP
protocol; only recipient acceptance created the requester listener. Roles reversed
between directions. Recipient processes had no reverse forwarding listener.

## HTTP video results

Ordinary Python HTTP/1.1 application servers served the same 57,952,254-byte video
through the proxy. This uses a mature application protocol instead of the old
custom file experiment. The receiver wrote/flushed/fsynced the file and independently
matched SHA-256. Goodput includes HTTP request/channel setup and final fsync, but
excludes membership, peer approval and initial QUIC establishment.

An existing persistent HTTP connection fetched a one-byte probe before and during
the transfer. These are application HTTP round-trip latencies, not ICMP ping or
the prior unreliable UDP RTT probes, so their values are not directly comparable.

| Path | File direction | Seconds | Goodput | Idle HTTP RTT median | Loaded HTTP RTT median / P95 | SHA-256 |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| direct-only | cellular-to-wifi | 41.86 | 11.08 Mbps | 101.6 ms | 377.7 / 614.0 ms | match |
| direct-only | wifi-to-cellular | 53.99 | 8.59 Mbps | 100.0 ms | 455.6 / 670.8 ms | match |
| relay-only | cellular-to-wifi | 43.06 | 10.77 Mbps | 99.8 ms | 365.4 / 598.3 ms | match |
| relay-only | wifi-to-cellular | 52.73 | 8.79 Mbps | 100.1 ms | 461.8 / 665.9 ms | match |

All direct trials recorded zero relayed application messages; forced-relay trials
recorded zero direct application messages. Data was not sent over ADB, SSH or
loopback between the peer daemons; only application ingress/egress and HTTP
management are local. The cellular USB hop transports UDP directly, not over TCP.

## UDP results

Each row requested a fresh UDP port grant and exchanged 30 distinct 128-byte
echo datagrams. This is a functional/reachability test, not a UDP capacity test.

| Path | Echo requester | Received | Echo RTT median / P95 |
| --- | --- | ---: | ---: |
| direct-only | wifi | 30 / 30 | 100.0 / 102.0 ms |
| direct-only | cellular | 30 / 30 | 100.5 / 132.5 ms |
| relay-only | wifi | 30 / 30 | 100.1 / 110.3 ms |
| relay-only | cellular | 30 / 30 | 102.6 / 119.4 ms |

## Control pages and fixes

Three URLs were launched in Microsoft Edge. All three HTTP listeners were
verified reachable; browser screenshot/interaction automation was unavailable
because its runtime module was missing. No visual rendering verification is claimed.

- Wi-Fi client: http://127.0.0.1:8771
- Cellular client: http://127.0.0.1:8772
- Remote administrator local view: http://127.0.0.1:8773/admin

The administrator view is a temporary loopback test proxy that verifies the
upstream server CA and keeps its administrator credential on the backend.
It avoids installing a root certificate or disabling browser/server TLS checks.

Two operational findings were fixed: the new directory connection now honors
Wi-Fi binding and the cellular adapter, and each daemon uses a distinct browser
cookie name because cookies are not isolated by port. A shared cookie-jar test
verified both clients remain authenticated concurrently. The two client listeners
were restarted and all three Edge URLs reopened after the cookie fix. The video
measurements used explicit per-client API credentials and were unaffected by it.

## Interpretation and cleanup

The new port-scoped proxy works over both physical exits, both directions and
direct/relay paths. Within these sequential runs, relay goodput was close to
direct. Loaded reliable HTTP latency rose substantially above idle. Reliable
channels share an ordering domain; this test does not isolate queueing sources.
It does not establish capacity, a universal relay penalty, or an isolated
performance comparison against the old custom file protocol.

The first completed phase stopped after two valid direct-video trials and one
UDP trial because its driver submitted a new request before the peer-busy update
arrived. The driver was corrected to wait for availability. The continuation reused
the same client daemons, reran both direct UDP directions, and completed relay tests.
The earlier setup-only folder dual-proxy-20261025-231831 transferred no file and
is excluded; it had incorrectly assumed a fixed carrier public IP.

Temporary admission entries were revoked and removed with key-matching checks.
All original membership state was preserved, apart from the deliberate removal
of retired peer-list fields during migration. The old /etc registry SHA-256 stayed
`8401d996369615c0d5080b0dd575dfb649e880444c7ecb64ca32bc079ed3485f`.
The cellular helper and test application servers stopped. The upgraded coordinator
remains active; local control pages remain available with clients offline. Test
credentials/configuration are retained in ignored evidence directories.

Validation after the route/cookie fixes: **53 tests passed in 58.66 seconds**.

Evidence:

- `artifacts/dual-proxy-20261025-231903/`
- `artifacts/dual-proxy-20261025-232231/`
- `artifacts/dual-proxy-20261025-summary.json`
