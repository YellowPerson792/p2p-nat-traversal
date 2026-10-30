# Direct-Path Diagnosis and Verified Workaround

Dates: 2026-10-21 through 2026-10-22, Europe/Stockholm. Artifact directory names use UTC.

Cleanup note: the complete latest two-real-NAT result remains under
`artifacts/20261021-230632-two-real-nats/`. Earlier artifact paths and one-off
script paths in this historical report are preserved inside
`.local/archive/pre-two-real-nats.zip`, with a verified SHA-256 manifest alongside.
Historical commands using archived scripts are not current active entrypoints.

## Outcome

Phone NAT-boundary probe (2026-10-22 local): fresh Android tethering/BPF entries
prove that the VM and hotspot PC are parallel downstreams translated to cellular
address 192.0.2.3. Authenticated server replies observe 198.51.100.3 instead,
proving a further upstream translation. Thus at least two translation boundaries
exist from either downstream client, with at least one outside the handset;
additional carrier-side NAT layers cannot be counted from these observations.
UDP and TCP traceroute reach the server at TTL 13, which is a route-hop result,
not a NAT count. Evidence: `artifacts/20261021-233144-phone-nat-layers/README.md`.

Shared-phone follow-up (2026-10-22 local): the PC used the phone hotspot
192.0.2.5 via 192.0.2.4 while the Debian VM used the same phone's cellular
exit. Both clients registered public IP 198.51.100.3. All four short auto runs
were direct (30/30 echoes each, zero server relay increments), with mean RTTs
172.80-181.73 ms and no authenticated unexpected-source endpoints. A relay-only
control returned 30/30 echoes with 60 server forwards. This contrasts with the
shared home-Wi-Fi failure: sharing a public IP alone does not explain failure,
but the end-host evidence does not locate the NAT turnaround or isolate a rule.
Full evidence: `artifacts/20261021-232756-shared-phone-network/README.md`.

Same-Wi-Fi follow-up (2026-10-22 local): with PC 192.0.2.13 and phone 192.0.2.12
behind gateway 192.0.2.10, all four auto runs failed public-endpoint punching
and successfully used relay (30/30 echoes each). Both clients registered the
same public IP 198.51.100.5; neither received peer probes or unexpected-source
packets. This reproduces the shared-exit failure with separate devices but does
not establish a remapping mechanism. Full evidence and scope are retained in
`artifacts/20261021-231922-same-wifi-phone-pc/README.md`.

Independent-network result (2026-10-22 local): the PC behind the home NAT and the phone Debian client
on cellular data achieved direct communication in two of four short auto-mode
runs. The other two runs automatically used relay; all returned 30/30 echoes.
WLAN capture confirms the direct data path, with no peer packets on Ethernet.
See "Two Real NATs" below. No additional forwarding rules were configured.

Earlier: public Ethernet plus a mobile hotspot achieved verified
external direct application communication in both client-role orientations.
Other sockets exposed different server-reflexive and peer-observed hotspot
endpoints without the previous shared-public-IP hairpin topology. See the
multi-interface experiment below. Reciprocal flow tuples alone are insufficient
to establish the earlier router's internal cause; that remains a hypothesis.

Latest controls found a repeatable **probe-order effect** with only A statically
forwarded. Across eight fresh B ports, A-first produced peer ports 1025-1028 and
failed at B's registered endpoint; B-first preserved B's port and allowed replies
to its registered endpoint. See the registration/filtering experiment below.
The server-facing mapping remained live throughout the failing cases.

Follow-up: the single-static-mapping experiment identified an actual peer-endpoint
mismatch. B registered as `198.51.100.5:54012`, but A received B's probes from
`198.51.100.5:1025`. Sending to 1025 reached B without a static forward on B.
Thus the earlier blanket description "dynamic hairpin does not work" is too
broad: dynamic hairpin worked to the peer-observed endpoint in this controlled
one-forward case. The earlier two-forward experiment below remains valid as a
workaround test, not a complete explanation of the original failure.

The initial failure occurred on UDP hairpin paths using server-observed endpoint
candidates. Public endpoint discovery and ordinary host-to-VM P2P work.
An explicit two-port UDP forward makes the same host-to-host public-endpoint
direct path work with DMZ still disabled. Removing the forward restores failure.

This establishes a working router-side workaround. It does not identify the
router firmware's internal rule or imply that every router needs forwarding.
This host test involves two mappings on one real NAT, not two independent NATs.
Same-NAT public-endpoint communication needs hairpinning; see
[RFC 4787, section 6](https://www.rfc-editor.org/rfc/rfc4787.html#section-6).

## Earlier Environment and Router State

- Native Windows clients use WLAN address `192.0.2.13`, gateway `192.0.2.10`.
- Observed public IP: `198.51.100.5`; coordination/relay VM: `198.51.100.1`.
- Router, verified through the existing authenticated Edge session:
  ZTE Xuntian AX3000, firmware `V1.0.0.7B6.8000`.
- DMZ disabled; UPnP disabled; initial forwarding table empty. A single optional
  debug rule is now retained with explicit user confirmation: `EP2120-debug-A`,
  UDP 54011 -> `192.0.2.13:54011`. B has no static forward. This is a debugging
  anchor, not a requirement or proof of traversal without router configuration.
- No Windows firewall rules or global router protection settings were changed.

The Edge connection initially failed because an updated browser runtime was
missing. The installed browser-repair skill restored version-matched components
and configuration with backups, then a supported browser-session reset restored
actual Edge control. Repair report:
`<LOCAL_BROWSER_REPAIR_REPORT>`.
The router's existing login was reused; no credentials were extracted.

## Follow-Up: Only A Has a Static Forward

Evidence: `artifacts/20261021-194316-single-mapping/result.json`, actor logs,
`traffic.pcapng`, and `packet-summary.json`. Only A had a temporary UDP 54011
forward to `192.0.2.13:54011`; B used local UDP 54012 without a static forward.
Both registered through the same sockets used for probes. Automatic peer probes,
automatic ACKs, and relay traffic were disabled in this diagnostic.

| Step | Sender and target | Received by target |
| --- | --- | ---: |
| 1 | A -> B's server-observed public port 54012, before B contacts A | 0 / 20 |
| 2 | B -> A's forwarded public port 54011 | 20 / 20 |
| 3 | A -> B's original public port 54012, after step 2 | 0 / 20 |
| 4 | A -> B's actual source port 1025, learned in step 2 | 20 / 20 |

A observed B's source as `198.51.100.5:1025`, not the registered port 54012.
B observed A's successful step-4 packets from `198.51.100.5:54011`.
WLAN capture confirms these translations and all four directional counts.
This rules out "merely send to the previously uncontacted address, then retry
the original candidate" as a sufficient fix in this run. Endpoint change is a
demonstrated part of the problem; the experiment alone does not establish the
router's general NAT classification or why it selected a different port.

The production client currently checks the expected peer source before processing
authenticated peer messages, so it cannot learn such a changed source. A future
fix should consider authenticated peer-reflexive discovery and explicit return
path validation. This run still required one reachable forwarded peer and does
not prove that learning alone can bootstrap two peers with no static forwards.

The single temporary rule was removed after the run; the forwarding table was
verified empty. Screenshot: `artifacts/single-map-cleaned.png`.
No production connection behavior or server deployment was changed for this test.

## Registration Persistence, Source Filtering, and Probe Order

Evidence from 2026-10-21, UTC directory names:

- `artifacts/20261021-200316-mapping-controls/`: initial no-forward control.
- `artifacts/20261021-200436-mapping-controls/`: extended no-forward control.
- `artifacts/20261021-200637-mapping-controls/`: only A forwarded, four B ports.
- `artifacts/20261021-200849-mapping-controls/`: four fresh B ports, reversed
  assignment of probe order to even/odd port numbers.

The latter three contain client-side `traffic.pcapng`, server-side `remote.pcap`,
`packet-evidence.json`, `summary.json`, and per-actor logs. Authenticated
registration replies and diagnostic probes include timestamps. The remote helper
uses unguessable run markers for raw network-path controls; these are separate
from the authenticated production protocol. No automatic ACK, automatic peer
probing, application relay, firewall changes, or server restart was involved.

### Server-Facing Mapping Remains Live

Without any forwarding rule, B registered from local/public port 54912 and
received 63/63 replies from `198.51.100.1:3478`, spanning 62.33 seconds with a
maximum reply gap of 1.023 seconds. Its public endpoint never changed, while A's
peer probes to that endpoint failed. With only A forwarded, B on port 55012
received 62/62 registration replies over 61.52 seconds, maximum gap 1.027 seconds,
even though its hairpin packets used source port 1025.

These are bounded observations under one-second outgoing registration refreshes,
not proof of indefinite reachability or idle timeout. They do rule out an expired
server-facing mapping as the explanation for these contemporaneous failures.

### Same Server IP, Different Source Ports

For B port 54912, and independently for B port 55012:

| Sender | Before B sends to that endpoint | After B sends to that endpoint |
| --- | ---: | ---: |
| Server IP, UDP 3479 | 0/5 | 5/5 |
| Server IP, UDP 3480 | 0/5 | 5/5 |

The destination was always B's original registered public endpoint. B's
registration replies from server port 3478 continued throughout. The VM observed
the same B public port on outbound traffic to 3478, 3479, and 3480, so opening
those paths did not require changing the destination public endpoint. Remote
capture confirms all five before-contact datagrams left the VM; local WLAN
capture contains none of them, while after-contact packets appear at both ends.
The VM's `nft list ruleset` was empty.

This is strong evidence for source-port-sensitive, established-flow filtering,
not merely source-IP filtering or an application rejecting an unexpected source.
It is consistent with address-and-port-dependent filtering in
[RFC 4787 section 5](https://www.rfc-editor.org/rfc/rfc4787.html#section-5).
Without router state-table access or a second remote public IP, it does not
fully characterize the router's general filtering/mapping implementation or
mathematically exclude every upstream middlebox.

### Probe Order Changes the Hairpin Mapping

A used the retained UDP 54011 forward; no B port was forwarded. Both actors used
their registration sockets and deliberately sent no automatic replies.

| B registered port | First peer sender | B source observed at A | A -> registered B | A -> actual B |
| --- | --- | --- | ---: | ---: |
| 55012 | A | 1025 | 0/20 | 20/20 |
| 55013 | B | 55013 | 20/20 | Same endpoint |
| 55014 | A | 1026 | 0/20 | 20/20 |
| 55015 | B | 55015 | 20/20 | Same endpoint |
| 55212 | B | 55212 | 20/20 | Same endpoint |
| 55213 | A | 1027 | 0/20 | 20/20 |
| 55214 | B | 55214 | 20/20 | Same endpoint |
| 55215 | A | 1028 | 0/20 | 20/20 |

B -> A succeeded 20/20 in every row. In A-first rows, A -> registered B failed
both before and after B's packets arrived; the long first row also failed after
the registration observation window. In B-first rows, the first A peer packet
was sent only after B's successful one-way batch. Reversing the even/odd-port
assignment reproduced the effect and excludes parity as its explanation.

The capture shows local B ports unchanged, outgoing TTL 128 and returning
hairpin TTL 127, with the NAT-translated source ports shown above. Changing only
the destination of A's reply to the observed low port delivered all packets.
Remote UDP 3479 could not reach these low hairpin ports (0/5 each); the first case
had already contacted 3479 successfully through its original server-facing port.
This further illustrates that neither external port is globally open to every
source/flow.

The best current mechanism hypothesis is that A's earlier unsuccessful flow
affects connection-tracking/NAT bindings, forcing B's later reverse flow to use
another source port. Returning to the original registered endpoint then selects
the wrong flow rather than B's successful hairpin return path. This is an
inference, not a captured router conntrack entry. Stateful NAT commonly caches
bindings from the first packet; see the
[nftables NAT documentation](https://wiki.nftables.org/wiki-nftables/index.php/Performing_Network_Address_Translation_%28NAT%29).
Implicit source-port changes to avoid clashes are described in the historical
[Netfilter NAT HOWTO](https://www.netfilter.org/documentation/HOWTO/NAT-HOWTO-6.html).
Neither source establishes which exact firmware/kernel implementation ZTE uses.

1025 is above the well-known/system range 0-1023. NAT source ports need not be in
the host's usual ephemeral range: RFC 4787 section 4.2.1 permits reassignment
within 1024-65535 for high source ports. The observed sequence 1025, 1026, 1027,
1028 supports allocation from a low-numbered fallback pool under these conditions;
it is not evidence of a local application binding 1025, nor a guarantee that future
allocations are predictable or that the router is universally a symmetric NAT.

### Reproduce and Current State

```powershell
# Configure/verify only A's debug forward separately; this script changes no router rules.
.venv-win\Scripts\python.exe scripts/registration_mapping_test.py --a-forward --port-base 55012
.venv-win\Scripts\python.exe scripts/registration_mapping_test.py --a-forward --port-base 55212 --hold-seconds 0 --capture-seconds 45 --reverse-order --skip-server-controls
.venv-win\Scripts\python.exe scripts/analyze_mapping_controls.py artifacts/<run-directory>
```

Use fresh B ports, or allow previous NAT flow state to expire, when reproducing
ordering experiments. For no-forward controls, disable the A rule explicitly
before running without `--a-forward`; that flag only records the precondition.
Do not change router rules during a run. Each run checks the service instance and
relay counter; all completed runs had zero relayed application packets.

Temporary VM helper files, UDP 3479/3480 listeners and capture processes were
removed. The production service remains active and was not redeployed. The
single A rule is intentionally retained per the user's later request, unlike the
earlier temporary rules. Screenshot: `artifacts/debug-map-active.png`.
Default clients still use ephemeral ports. The production protocol still needs
authenticated peer-reflexive candidate learning and return-path validation; a
single forwarded debugging endpoint does not prove both-unforwarded traversal.
Fifteen tests pass, including three new diagnostic authentication/no-ACK controls.

## Ordered Batches Using Only Registered Endpoints

Evidence: `artifacts/20261021-203352-ordered-registration/`, including
`summary.json`, per-case actor logs and scoped WLAN `traffic.pcapng`.
The local controller independently scheduled B -> A, A -> B, B -> A, A -> B.
Each batch had 20 signed diagnostic packets; no actor automatically probed or
acknowledged any peer packet. Local IPC, not a UDP handshake, observed delivery.
Every send explicitly targeted the initial server-registered endpoint; no
peer-reflexive retargeting or relay was used.

The Edge forwarding table was read-only verified during this experiment: only
`EP2120-debug-A`, UDP 54011 -> `192.0.2.13:54011`, was enabled. No router changes
were made.

| Condition | A local/public port | B local/public port | B -> registered A | A -> registered B |
| --- | --- | --- | ---: | ---: |
| Only A forwarded | 54011 | 56012 | 40/40 | 40/40 |
| Only A forwarded | 54011 | 56013 | 40/40 | 40/40 |
| Only A forwarded | 54011 | 56014 | 40/40 | 40/40 |
| Neither test port forwarded | 56021 | 56022 | 0/20 | 0/20 |

All three single-forward cases received packets at the exact expected public
source endpoints, and their server-facing endpoints stayed unchanged. Thus
registered endpoints were mutually usable in these B-first, one-reachable-peer
cases. The unforwarded-port control used fresh sockets outside the retained
rule, with the same deliberate B-first schedule, and neither direction arrived.
An absent packet does not reveal what its hairpin source translation would have
been; the negative control cannot by itself prove absence of reallocation.

The earlier A-first cases also had automatic ACKs disabled. Consequently the
supported distinction is **initial peer-send order with A reachable**, not merely
whether ACKs are enabled. This experiment does not establish two-unforwarded
traversal or prove the internal connection-tracking collision hypothesis.
The measured relay delta was zero. Production protocol and deployment unchanged;
all actors and the capture process stopped after the run. Fifteen tests pass.

```powershell
.venv-win\Scripts\python.exe scripts/ordered_registration_test.py --port-base 56012
.venv-win\Scripts\python.exe scripts/analyze_mapping_controls.py artifacts/<run-directory>
```

The runner assumes the separately verified A rule is enabled, never edits router
configuration, and uses three forwarded-anchor cases plus one unforwarded-port
control. Choose a fresh port base on repetition to avoid previous NAT flow state.

## Bounded Guesses of Low Hairpin Ports

Evidence: `artifacts/20261021-204139-guessed-mapping/`, generated by
`scripts/guess_mapping_test.py`. No router setting or production protocol was
changed. All candidate packets targeted only the clients' shared server-observed
public IP, from their original registration sockets. Automatic peer probes and
ACKs remained disabled. Authenticated run/peer/phase identifiers distinguished
actual peer delivery from unrelated router replies or stale traffic.

Each case first sent 20 packets in each direction to registered endpoints, then
performed two fixed sweeps over public UDP 1025-1032, three packets per candidate
per direction per sweep. Candidate selection did not adapt to observed sources.
Local IPC scheduled every batch. Guess traffic can itself alter NAT state, so a
hit demonstrates reachability at that phase, not necessarily the pre-sweep state.

| Case | A / B local ports | Initial sender | Registered-endpoint delivery | Guessed-port delivery |
| --- | --- | --- | --- | --- |
| Neither test port forwarded | 57031 / 57032 | B | Both directions 0/20 | Both directions 0/48 |
| Neither test port forwarded | 57033 / 57034 | A | Both directions 0/20 | Both directions 0/48 |
| Only A forwarded, positive control | 54011 / 57035 | A | A -> B 0/20; B -> A 20/20 | A -> 1028 reached B 6/6; other candidates no hits |

In the positive control, A observed B's original 20-packet batch from
`198.51.100.5:1028`, while B continued registering as port 57035. The independently
scheduled guesses reached B only at 1028, in both sweeps, with source A's expected
public port 54011. A -> 1025 did not reach B. Thus the method detected a known
remapped path, and the reassigned port was not invariably 1025.

All server-facing registered endpoints remained stable; relay delta was zero.
The two unforwarded cases yielded no actual peer source observations. Their
negative results establish only that this finite candidate set and schedule did
not reveal a reachable remapped path. They do not distinguish absent remapping,
a port outside the guessed range, or mappings whose source/flow filters still
prevent these probes. Reading the router's conntrack/NAT state would provide a
more direct distinction; source-port guesses alone cannot prove absence.

```powershell
.venv-win\Scripts\python.exe scripts/guess_mapping_test.py --port-base 57031
.venv-win\Scripts\python.exe scripts/analyze_mapping_controls.py artifacts/<run-directory>
```

Use fresh local ports on repeated runs. The retained A-only UDP 54011 rule is a
precondition of the final positive control, not either unforwarded case. The
script caps the candidate set at 16 ports and never modifies router rules.
Capture and actor processes were stopped after the run; fifteen tests pass.

## Near-Simultaneous Probes with a Shared Local Deadline

Evidence:

- `artifacts/20261021-205019-simultaneous-mapping/`: initial monotonic-clock run.
- `artifacts/20261021-205150-simultaneous-mapping/`: high-resolution repeat.

The first run exposed a Windows clock limitation: `time.monotonic()` used
GetTickCount64 with 15.625 ms resolution. Its six simultaneous single-forward
cases succeeded 3/6, but application timestamp equality was not evidence of
simultaneous packet departures. The repeat used `time.perf_counter()` backed by
QueryPerformanceCounter (reported resolution 100 ns), with both actor commands
queued before a common future deadline. Each actor waited on a worker thread,
leaving its receive loop active. No global timer settings were changed.

Each repeat case sent 30 authenticated packets per direction, scheduled at 20 ms
intervals. Both targets remained their server-registered public endpoints, with
no automatic ACKs, automatic probes, or learned-endpoint retargeting. The runner
recorded actual first-send application times and first outgoing packet capture
times. These measure host departure timing, not router processing order or
exactly simultaneous arrival at the router.

High-resolution repeat, using fresh B ports 58231-58240 with A on 54011, and
fresh pairs 58251-58270 outside the retained forward:

| Configuration | Scheduled start | Trials | Bidirectional success | Both first packets received |
| --- | --- | ---: | ---: | ---: |
| Only A forwarded | Same deadline | 6 | 4/6 | 4/6 |
| Only A forwarded | A leads by 50 ms | 2 | 0/2 | 0/2 |
| Only A forwarded | B leads by 50 ms | 2 | 2/2 | 2/2 |
| Neither test port forwarded | Same deadline | 6 | 0/6 | 0/6 |
| Neither test port forwarded | A leads by 50 ms | 2 | 0/2 | 0/2 |
| Neither test port forwarded | B leads by 50 ms | 2 | 0/2 | 0/2 |

Every bidirectionally successful case delivered all 30 packets each way. In the
two failing simultaneous single-forward cases, A still received 30/30 from B,
but B's observed public source had changed to 1032 or 1033; A -> B's registered
endpoint delivered 0/30. A-leading single-forward cases similarly used 1034 and
1035. B-leading cases preserved B's original public port.

Thus near-simultaneous submission can sometimes avoid the failing outcome in
the one-reachable-peer setup, but did not consistently prevent reallocation or
establish two-unforwarded traversal. This small sample is not an estimate of
general NAT traversal probability, and does not prove the internal conflict
mechanism. Failures under the unforwarded condition still provide no received
peer source from which to infer whether remapping happened.

No router changes or production deployment occurred; relay delta was zero in
both runs. All actors and capture processes stopped after the runs. Sixteen
tests pass, including a high-resolution barrier deadline check.

```powershell
.venv-win\Scripts\python.exe scripts/simultaneous_mapping_test.py --port-base 58231
```

The script automatically analyzes the capture and writes `summary.json` with
application/capture skew and grouped outcomes. Use a fresh port base on repeat.

## Public Ethernet Plus Mobile Hotspot (2026-10-22 Local)

| Interface | Windows index | Local IPv4 | Gateway |
| --- | ---: | --- | --- |
| Ethernet | 12 | 198.51.100.7 | 198.51.100.6 |
| WLAN / phone hotspot | 26 | 192.0.2.5 | 192.0.2.4 |

Ethernet has public IPv4 directly assigned to the host; the server observed the
hotspot exit as 198.51.100.3. This is a public endpoint plus a mobile-NAT path,
not two private hosts behind independently verified NAT devices. Phone and
carrier translation layers were not individually inspected. The earlier ZTE
debug forward is outside this path and none of its ports were used.

The local client now optionally pins both UDP and TLS control sockets using
Windows `IP_UNICAST_IF` and binds both to `--bind-host`. The interface index is a
network-order DWORD, per the
[Microsoft documentation](https://learn.microsoft.com/en-us/windows/win32/winsock/ipproto-ip-socket-options).
`--interface-index` is native-Windows-only; unspecified interfaces retain default
routing. Explicitly pinned control connections bypass proxies but retain pinned
certificate validation and token authentication. No global route, interface
metric, Windows firewall or router settings were changed. The local client was
updated; the remote service was not redeployed.

### Application Tests and Physical-Path Proof

Evidence: `artifacts/20261021-220159-multi-interface/summary.json`, logs/CSV,
`traffic.pcapng`, and `packet-evidence.json`. Capture interface 0 was Ethernet,
1 WLAN, and 2 the Npcap loopback adapter.

| Benchmark origin | Mode | Local UDP ports (benchmark / echo) | Returned / sent | Server relays | Mean RTT | Setup |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| Ethernet | auto -> direct | 52031 / 52032 | 118/120 | 0 | 131.95 ms | 0.781 s |
| Hotspot | auto -> direct | 52033 / 52034 | 120/120 | 0 | 129.92 ms | 0.953 s |
| Ethernet | relay-only | 52035 / 52036 | 119/121 | 240 | 135.55 ms | 1.781 s |

Each benchmark ran six seconds at nominal 20 application messages/s, 256-byte
payloads and a two-second drain. Counts are measured, not inferred from rate.
These are application echoes, not system ping. The short runs do not establish
that direct mode is always faster or loss-free.

The auto cases registered hotspot endpoints 198.51.100.3:47144 and :47149;
those same endpoints appeared in successful peer traffic. Hotspot packets left
WLAN as 192.0.2.5 and entered Ethernet from the translated public endpoint,
TTL 119. Reverse business traffic left Ethernet for the hotspot public endpoint
and arrived on WLAN addressed to 192.0.2.5, TTL 120. UDP registrations and
each TLS control socket also appeared on their selected physical interfaces.
Scoped loopback test packet count was zero. Both auto cases have
`physical_direct_path_verified: true`; logical direct counters alone are not
accepted as physical-path proof.

Initial run `artifacts/20261021-220038-multi-interface/` used 59031-59036. Its
first auto case fell back to relay (120/120 returned, 240 server relays): the
hotspot registered as :47140 but authenticated probes arrived from :47155.
The later two cases could not bind: Windows' excluded UDP range 59033-59132
caused WinError 10013. Those are setup failures, not connectivity results.
Repeating on available ports 52031-52036 completed all three cases. No excluded
range was removed.

### Authenticated Actual-Endpoint Diagnostic

Evidence: `artifacts/20261021-220315-multi-interface-probe/`, native processes
on Ethernet UDP 52041 and hotspot UDP 52042. Automatic probes and ACKs were
disabled; only signed messages with current pair identities/epoch were recorded.
The hotspot registered as 198.51.100.3:47162, but Ethernet received its probes
from :47152. Registration remained at :47162 throughout.

| Stage | Delivered |
| --- | ---: |
| Ethernet -> registered hotspot :47162, before contact | 0/20 |
| Hotspot -> registered Ethernet 198.51.100.7:52041 | 20/20 |
| Ethernet -> registered hotspot :47162, after contact | 0/20 |
| Ethernet -> authenticated actual hotspot :47152 | 20/20 |

Both successful directions appear on physical interfaces with public translation,
not loopback; server relay delta was zero. This demonstrates a direct diagnostic
path after learning the actual source, not production ICE/peer-reflexive support.
Production still rejects unexpected sources. The mismatch is not universal:
the auto application tests above had usable registered endpoints. These results
do not identify whether phone/carrier NAT, allocation conflicts, timing, or a
combination caused the discrepancy, nor establish the earlier hairpin mechanism.

### Reproduce

```powershell
Get-NetIPConfiguration
& 'C:\Program Files\Wireshark\dumpcap.exe' -D
.venv-win\Scripts\python.exe scripts/multi_interface_test.py --ethernet-host 198.51.100.7 --ethernet-index 12 --ethernet-capture '\Device\NPF_{88BFF329-58D4-4112-8F19-837F13F576CA}' --hotspot-host 192.0.2.5 --hotspot-index 26 --hotspot-capture '\Device\NPF_{EA4625B5-411F-448F-BB57-E764FAF77917}' --port-base 52031
.venv-win\Scripts\python.exe scripts/multi_interface_probe.py --ethernet-host 198.51.100.7 --ethernet-index 12 --ethernet-capture '\Device\NPF_{88BFF329-58D4-4112-8F19-837F13F576CA}' --hotspot-host 192.0.2.5 --hotspot-index 26 --hotspot-capture '\Device\NPF_{EA4625B5-411F-448F-BB57-E764FAF77917}'
```

Addresses, capture GUIDs, interface indices, excluded ranges and hotspot public
mappings are environment-specific; recheck before repetition. All clients and
capture processes exited. Twenty-four tests pass, including network-order
binding, failed-socket cleanup and capture-proof rejection when physical evidence
is missing or loopback test traffic is present.

## Packet-Level and Remote-Peer Controls

Evidence: `artifacts/20261021-190008-diagnostic/` and
`artifacts/20261021-190210-diagnostic/`.
Each contains native client logs, remote client results, WLAN capture, and
`packet-summary.json` generated by `scripts/analyze_nat_capture.py`.

The first capture recorded both clients' outgoing public-endpoint probes but no
returning probes. The second capture additionally matched direct-protocol UDP
payloads regardless of whether their source was the public IP or a rewritten
private/router address. Again, neither host client received a returning peer
probe. This places the observed loss before the application's source-address
validation, at the NAT/hairpin network path rather than that validation check.

For the remote control, a temporary echo client ran on the VM itself, using its
own UDP socket distinct from server port 3478. The native host client and VM echo
client exchanged 161/161 direct application echoes, with zero relay datagrams.
Mean RTT was 23.59 ms. The VM peer used `198.51.100.1:50936`; the local public
endpoint was `198.51.100.5:55190`. Incoming probes, matching acknowledgements,
and data in both directions appear in the WLAN capture. A second control passed
80/80 direct echoes using fresh sockets.

The remote echo process and its generated `/tmp/p2p-diag-*` files were removed
after each test. Captures include only scoped UDP test traffic and no TLS control
credentials. The router's own UPnP page confirmed UPnP was off; unsuccessful
automatic discovery alone was not treated as proof of absent hairpin support.

## Controlled Router Experiment

Both clients used fixed local ports 54001 and 54002. The server observed the same
public port numbers. Application code, public endpoints, and DMZ state were held
constant across the three phases. The temporary rule was explicitly approved by
the user immediately before it was submitted in Edge.

Temporary rule: `EP2120-temp-UDP`, **UDP only**, WAN ports `54001-54002` to
`192.0.2.13:54001-54002`. No other port ranges or protocols were enabled.

| Phase | Artifact directory | Setup | Probes sent/received at A | Echoes | Relay packets |
| --- | --- | ---: | ---: | ---: | ---: |
| No forward | `20261021-191102-host` | 5.156 s | 21 / 0 | 80 / 80 relayed | 160 |
| Two-port forward active | `20261021-191235-host` | 0.140 s | 11 / 11 | 160 / 160 direct | 0 |
| Forward deleted | `20261021-191332-host` | 5.172 s | 21 / 0 | 80 / 80 relayed | 160 |

Mean RTT with the rule was 3.66 ms, compared with 46.84 ms before and 47.22 ms
afterward through the relay. No authenticated unexpected-source packets or UDP
errors were observed in these phases. Each phase used the same executable and
source version; no client patch was introduced between the phases.

## Earlier Two-Port Experiment Cleanup

The temporary forwarding rule was deleted immediately after the successful test.
The empty forwarding table, disabled DMZ, and disabled UPnP were verified in Edge.
The final same-port test again used relay, independently confirming restoration.
At that point the router had no retained test forwarding rules. The later
user-requested single debug rule is documented above. Default clients still fall
back to relay when their direct checks cannot establish a validated path.

Screenshots:

- Prepared rule: `artifacts/port-forward-review.png`.
- Rule active: `artifacts/port-forward-active.png`.
- Final restored state: `artifacts/port-forward-cleaned.png`.

## Program Changes and Reproduction

The client now exposes `--bind-port` as well as `--bind-host`. Its logs and JSON
summaries include probes sent/received, valid probe responses, unexpected source
counts, authenticated mismatched sources, and UDP errors. Periodic direct retry
windows produce explicit log events. Source validation and HMAC checks were not
weakened. Twelve focused tests pass.

To repeat the two-mapping test without changing router state:

```powershell
.\scripts\run-host.ps1 -Duration 8 -PortA 54001 -PortB 54002 -Scenario auto
```

The command creates no router mapping. The successful forwarding workaround
requires configuring the exact rule separately. Default clients retain random
ephemeral ports, so a fixed router rule must be paired with fixed client ports.

To reproduce packet capture and the VM-peer comparison, enumerate capture
interfaces and supply the actual WLAN interface ID:

```powershell
& 'C:\Program Files\Wireshark\dumpcap.exe' -D
.venv-win\Scripts\python.exe scripts/diagnose_nat.py --interface 8 --public-ip 198.51.100.5
python scripts/analyze_nat_capture.py artifacts/<diagnostic-directory>
```

The interface ID and public IP are environment-specific. This diagnostic requires
the installed Wireshark/Npcap capture capability and existing SSH access to the VM.

The earlier fixed-port diagnostics source snapshot was
deployed as `/opt/p2p-overlay/releases/20261021-191425`; systemd health passed.
The later multi-interface client changes remain local and have not been deployed.

## Two Real NATs (2026-10-22 Local)

Artifacts: `artifacts/20261021-230632-two-real-nats/` (UTC timestamp).
The PC was pinned to WLAN interface 26, `192.0.2.13`, gateway `192.0.2.10`,
for both UDP and TLS control sockets. Its server-observed public endpoint was
`198.51.100.5:<local-port>`. The public Ethernet connection remained present but
was not used by either client socket. Android reported CELLULAR as its active
default network; the Debian VM used `192.0.2.2` via `192.0.2.1`, and
the server observed the phone as `198.51.100.3`.

USB/ADB carried SSH management only. The unchanged phone zipapp was used;
no packages, router mappings, VM forwarding rules, or global routes were added.
The retained optional debug rule on UDP 54011 was not used. All application
messages were 256 bytes, at 10 messages/s for 3 seconds. These are application
echoes, not system ping measurements.

| Run | Benchmark sender | PC / phone local ports | Mode | Observed path | Echoes | Server relay packets | Mean RTT |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | PC | 52051 / 52052 | auto | direct | 30/30 | 0 | 119.27 ms |
| 2 | Phone | 52053 / 52054 | auto | relay after timeout | 30/30 | 60 | 135.86 ms |
| 3 | PC | 52055 / 52056 | auto | relay after timeout | 30/30 | 60 | 128.70 ms |
| 4 | Phone | 52057 / 52058 | auto | direct | 30/30 | 0 | 123.96 ms |
| 5 | PC | 52059 / 52060 | relay-only control | relay | 30/30 | 60 | 128.67 ms |

Direct setup took approximately 0.80-0.94 seconds. Failed punching switched to
relay after approximately 5.60-6.03 seconds including control setup. Each run
used distinct node names and local ports. All client processes exited zero and
the server instance remained unchanged across each counter measurement.

`traffic.pcapng` captured WLAN and Ethernet concurrently. `packet-evidence.json`
contains the decoded endpoint/count evidence: the two direct runs contributed
120 direct business datagrams on WLAN (30 in each direction per run), with zero
test peer packets on Ethernet. The raw per-client logs and `results.json` retain
registration endpoints, probe counts, path transitions, and summaries.

Both unsuccessful auto runs sent probes but recorded zero incoming peer probes
on both clients, with no unexpected-source packets or authentication failures.
This is not evidence of the previously observed source-port mismatch: without
an arriving peer packet or NAT-state observation, mapping variation and filtering
cannot be distinguished here. The small sample demonstrates feasibility and
automatic fallback, not a general success rate or a guaranteed port selection.
