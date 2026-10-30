# Verification Record

## Publication preparation and forwarding optimization (2026-10-30)

The final full suite passed **106 tests in 118.76 seconds**, including the latest
configuration, maintenance scheduling and forwarding changes.
Coverage includes multi-window transfers, independent streams under loss,
direct/relay switching, half-close, receiver backpressure, UDP sender reuse and
index cleanup. See [proxy performance](../performance/PROXY_PERFORMANCE.md) for local
microbenchmark scope and [publication privacy](REPOSITORY_PRIVACY.md) for
cleanup, anonymization and the remaining Git-history findings. Historical
sections below describe their named revisions, not current wire compatibility.
No remote deployment or push was performed.

## Open-source installation and packaging (2026-10-30)

Added the MIT license, editable package entry points, locked bootstrap workflow,
versioned client/server configuration, doctor diagnostics, public examples and
contributor instructions. Existing tests, local state, CLI/HTTP interfaces and
developer diagnostics remain available. New version-1 paths resolve beside the
configuration; legacy configurations keep their previous path behavior.

Bootstrap completed in an isolated local environment. Wheel and source builds
succeeded; archive inspection confirmed browser resources and diagnostic modules
in the wheel, plus tests, examples and bootstrap files in the source archive.
Installed the wheel into that environment and ran both CLI entry points from a
temporary directory outside the checkout. Version, doctor, client/server config
checks, installed resource lookup and pip dependency checks passed. This caught
and fixed the diagnostic application's import of a removed transport constant;
its existing 512-task bound is now defined within the diagnostic application.
Compilation, current working-file privacy scanning and whitespace checks passed.
The Windows/Linux Python 3.12/3.13 CI matrix is configured but has not run remotely.

Git history retains the findings documented in
[repository privacy](REPOSITORY_PRIVACY.md). These checks do not establish
that the existing history is ready for public release. No history rewrite,
remote deployment or push was performed.

## Adaptive maintenance and isolated control heartbeat (2026-10-30)

Moved WebSocket heartbeat, certificate renewal and idle channel-budget renewal
out of the QUIC data task. Heartbeat sends/closes are bounded and canceled with
the control session; data maintenance only signals closure rather than awaiting
WebSocket backpressure. Replaced the fixed 10 ms data poll with event/deadline
wakeups and an idle one-second safety check, retaining earlier QUIC pacing,
ACK, recovery and maintenance deadlines.

Empty NAT hints now back off during bootstrap and stop after authentication.
Selected-carrier probes run every second when active and every three seconds
when idle; healthy backup relay probes use five seconds; failed backup direct
probes expire and back off up to four seconds. Removed standalone periodic QUIC
PINGs. Active/idle freshness windows are three/seven seconds, with immediate
probing and a grace period on idle resume. Registration backs off from about one
second to five seconds after authenticated replies, bounded by server TTL.

Seven maintenance tests include actual QUIC loss recovery while heartbeat send
is blocked, idle event wakeups, stopped/backed-off hints, registration adaptation,
idle probe budgeting, bounded direct retry, and idle survival beyond QUIC's
10-second native timeout followed by successful application delivery. The deterministic 31-second
healthy-idle policy test allows at most 18 carrier probe requests versus the old
62 carrier probes + 31 standalone PINGs, excluding replies/ACKs/packet coalescing.
This does not establish a real-network CPU, power, latency or throughput gain.
Existing migration tests allow the newly intentional direct recovery backoff.
The full regression suite passed: **89 tests in 110.79 seconds**. A subsequent
run including the new long-idle case passed all **7 maintenance tests in 13.82
seconds**. Compilation, whitespace checks, current maintenance-document links
and the phone client zipapp build passed. No CPU, battery or WAN speedup was
measured. The working tree also contains concurrent changes outside this task;
those were preserved.

Wire ALPN remains `overlay-quic-v8`; no coordinator wire upgrade is required for
this scheduling change alone. No remote deployment was performed. Control-loss,
directory-loss and revocation still close applications; no offline lease policy
or shared device-wide transport is introduced. See [maintenance](../architecture/MAINTENANCE.md).

## Relay allocations replace pair epochs (2026-10-30)

Replaced the epoch and pair-by-SID routing table with `RelayAllocation` records
indexed by a unique server-generated allocation ID, each binding two exact
Session objects for the same approved port consent. Removing either session
invalidates the allocation and clears both endpoint references. New peers get
new IDs even when the other endpoint keeps its SID. UDP endpoint updates keep
the allocation. Data packets route by allocation ID + source side rather than
SID followed by a lookup of the latest peer. HMAC, registered-source, live
session/authorization and replay checks remain.

The same allocation ID scopes direct packets, client identity proofs and local
channel lifetime. QUIC retains independent native CID and stream responsibilities;
the relay does not decrypt or authenticate the inner QUIC connection. SID remains
in registration and signed certificate binding, not every relay packet. There
is no separate epoch field. OD3 direct framing stays 19 bytes; OR3 relay framing
is 28 bytes + 32 bytes HMAC, saving 15 bytes per relay datagram versus OR2.
Maximum UDP payloads are 1219 / 1260 bytes. ALPN is `overlay-quic-v8`; coordinator
and both endpoints require a coordinated upgrade/restart. No remote deployment
or new real-network performance measurement was performed.

Tests cover replacement of one peer without replacing the sender SID, invalid
old allocations, wrong-side HMAC keys, reverse forwarding, address updates,
cross-consent allocation refusal and stale/wrong-side replies not poisoning the
client replay window. The full suite passed: **82 tests in 79.82 seconds**.
Compilation, whitespace checks, current documentation links and the phone client
zipapp build passed. The archive is `.local/phone/client.pyz`.
See [allocation design](../architecture/RELAY_ALLOCATIONS.md).

## Compact direct / relay envelopes (2026-10-30)

Removed the redundant 8-byte original destination CID from both UDP envelopes.
`OD2` carries marker + epoch + opaque QUIC; `OR2` carries marker + SID + replay
sequence + epoch + opaque QUIC + HMAC. Session routing, authorization, source
validation, epoch checks and replay protection remain. Native QUIC performs CID
checks/rotation; the local original CID remains in the client proof context and
diagnostic status. The single connection, streams and carrier recovery remain.

Direct headers shrink from 27 to 19 bytes; relay headers from 51 to 43 bytes,
plus the unchanged 32-byte HMAC. Maximum UDP payloads shrink to 1219 / 1275 bytes.
ALPN is `overlay-quic-v7`; the coordinator and both endpoints must upgrade/restart
together. Old binary envelopes fail closed with no fallback. No remote deployment
or new real-network performance measurement was performed.

Verification covers compact packet boundaries, opaque payload preservation,
HMAC tampering across routing/payload fields, rejection of old envelopes and
native rejection of an unrelated Initial at the same endpoint/epoch without
interrupting the existing connection. The full suite passed: **77 tests in
80.10 seconds**. Compilation, whitespace checks, documentation links and the phone
client zipapp build passed. The archive is `.local/phone/client.pyz`.

## Restored client identity challenge, public TLS APIs (2026-10-30)

Removed `overlay/quic_tls.py`, its mandatory-client-certificate hooks and the
experimental mTLS-only tests. Normal QUIC/TLS authenticates the server through
the signed device-bound certificate. HELLO / CHALLENGE / PROOF / READY on stream
0 authenticates the client with its long-term approved Ed25519 identity. Proofs
bind node names, epoch, original destination CID, server certificate hash and
fresh nonces. No application callback runs before identity verification; early
reliable streams wait for READY and early DATAGRAMs are discarded. Public
cryptography APIs check bound certificate validity and server-authentication EKU.

The single native QUIC connection, CID rotation, direct/relay carrier switching,
independent data streams and bounded native-buffer backpressure are retained.
Client/server uni control streams 2 / 3 carry path state and abort notices; data
streams begin at 6 / 7. No custom application-data sequence/ACK/replay layer was
reintroduced. The read-only aioquic buffer adapter remains; private TLS mutation
has been removed. ALPN is `overlay-quic-v6`; both peers must upgrade/restart.
No coordinator upgrade is needed solely for this opaque transport change.

Verification covers bad server keys/certificates, certificate validity/purpose,
a wrong client identity key, replayed client proofs, mismatched challenge context,
and a delayed READY overtaken by an independent encrypted data stream.
The full suite passed: **74 tests in 77.65 seconds**. Compilation, whitespace
checks, documentation links and the phone client zipapp build also passed.
No remote deployment or new real-network performance measurement was performed.

## Superseded experiment: mutual TLS authentication (2026-10-30)

Peer authentication now completes in QUIC/TLS 1.3. Both peers present their
short-lived device-bound certificates; native CertificateVerify / Finished prove
key possession. Exact certificate pinning, validity and role-specific EKU checks
are enforced in both directions. Missing client certificates and a server that
omits CertificateRequest fail closed. Device-key signatures still delegate to
the session certificates; enrollment, independent identity pins and port consent
remain separate permission/trust checks.

Removed the stream-level HELLO / CHALLENGE / PROOF / READY exchange and its
nonce/context/signature code. The first uni stream from each endpoint (2 / 3)
now carries path state and abort notices. Application streams start at 6 / 7.
One native QUIC connection and all streams still survive direct/relay switching.
ALPN is `overlay-quic-v5`; both peers must upgrade/restart. The coordinator's
opaque relay and identity-binding protocol need no upgrade for this change.

The adapter in `overlay/quic_tls.py` is explicitly restricted to aioquic 1.7.0.
It uses private per-context hooks for certificate requests and strict acceptance;
it does not patch the installed library or implement a custom signature protocol.
Dependency upgrades must revalidate this boundary. TLS resumption / 0-RTT remain
disabled. The client may finish locally before the server accepts its certificate;
server application delivery remains gated by successful mutual TLS completion.

Validation: 12 adversarial real-QUIC handshake tests cover client/server wrong
keys, substituted certificates, expired/future certificates, wrong TLS purpose,
empty client certificates and omitted CertificateRequest. Transport tests also
exercise loss of the client certificate/Finished flight without early server
application delivery. The full suite passed: **75 tests in 80.94 seconds**.
A subsequent targeted check also verified the standard TLS `certificate_required`
alert for an empty client certificate. Compilation, whitespace checks and the
phone client zipapp build passed. The new archive is `.local/phone/client.pyz`.
No remote deployment or new real-network performance measurement is claimed.

## Superseded authentication: shared QUIC carrier switching (2026-10-30)

This entry supersedes the dual-connection recovery design below. Each approved
port instance now keeps one aioquic connection across direct/relay switches.
The original destination CID replaces the random per-path envelope token;
active CIDs can rotate without changing native streams. Stream 0 carries device
authentication/abort notices, stream 4 carries authenticated path state, and
independent unidirectional data streams carry each application direction.
Overlay message sequencing, cumulative ACKs, receive ordering maps and pending
replay caches were removed. Only channel lifecycle and native-buffer admission
limits remain. The aioquic 1.7.0 buffer adapter reads private occupancy fields;
it never mutates library state. UDP and probe queues are bounded.

Validation: **63 tests passed in 71.81 seconds**. Deterministic real-QUIC routing
tests cover a blocked relay during bootstrap, direct failure and recovery with
lost data/FINs, unchanged connection/stream identity, CID rotation, old-packet
deduplication, asymmetric carrier choices, native-buffer backpressure and
wrong-carrier probe replies. Real loopback coordinator/TCP tests cover two
persistent application sockets through direct-only/relay-only/auto selection;
closing one leaves the second working. Existing identity, approval, multi-port,
UDP, half-close, stream-isolation and zipapp checks also passed.

Compilation and phone zipapp build passed. No remote coordinator deployment,
phone installation or real-network performance measurement was performed.
Both peers must upgrade/restart for ALPN `overlay-quic-v4`; the coordinator's
opaque wire envelope is unchanged. This implementation switches carriers below
a stable logical QUIC address. It does not provide native address migration,
Multipath QUIC, independent path congestion state or restart-safe resume.
See [current transport design](../architecture/MULTISTREAM_TRANSPORT.md).

## Superseded: per-channel recovery over two QUIC connections (2026-10-30)

Each proxy application channel now has independent logical sequencing, cumulative
ACKs, receive ordering, send windows and pending replay. Each direct/relay link
maps that ID to a pair of unidirectional QUIC streams. Stream 0 carries only
authentication, channel-specific ACKs and aborts. UDP payloads remain DATAGRAMs.
Proxy records must name the same channel as their authenticated stream binding.
TCP half-close preserves the reverse direction; normal completion sends FIN
after logical acknowledgement, while abort discards only that channel. Closed
IDs and parser/record buffers are bounded. A 4096-channel lifetime budget renews
the data-control session only after channels finish. Port-instance approvals and
their independent tunnels are unchanged.

Validation: **65 tests passed in 66.22 seconds**, followed by two additional
passing tests for delayed authentication READY and rejection of old ALPN.
Coverage includes an actual encrypted packet dropped on one stream while a second
stream delivers first, per-channel window/ACK isolation, replay on switching,
two concurrent TCP connections switching direct/relay, half-close, channel-key
mismatch, 80 sequential streams, partial-BIND reset cleanup and FIN replay after
replacement with no unacknowledged data. Compilation, diff whitespace checks and
the phone zipapp build passed. The new package is `.local/phone/client.pyz`.

TLS ALPN is now `overlay-quic-v3`; both peers must upgrade/restart. The signed
identity suite and coordinator's opaque relay envelope are unchanged, so no
coordinator deployment was needed or performed for this change. No real-network
throughput/latency improvement or phone installation is claimed. See
[multi-stream transport](../architecture/MULTISTREAM_TRANSPORT.md) for bounds and remaining
shared congestion/control-stream dependencies.

## Device identity and independent port connections (2026-10-27)

One device identity/directory session now manages multiple independent connection
instances. Each instance owns its exact port/protocol/direction consent, data
client, UDP socket, direct/relay QUIC links and requester listener. The coordinator
keys data sessions by consent ticket and node, preventing another port on the same
device from replacing or pairing with it. Connection IDs scope close/cancel/error
messages. Closing one row leaves others alive; device leave/revocation closes all
affected rows. The combined per-device active/pending limit is 16, with global
limits of 64 pending requests and 256 active consents. Browser and CLI use the
same APIs; status now has `connections[]`, and `ctl close --id ID` closes one row.

Validation: **55 tests passed in 71.59 seconds**. After explicitly allocating an
ephemeral UDP socket per instance, the five device/multi-instance tests passed
again. The new integration test runs three real devices, two simultaneous TCP
ports and one UDP port on B, and a TCP port on C. It validates direct/relay
transfer, pending approvals, targeted close/cancel, unauthorized third-device
close, quota enforcement, local listener conflict isolation, selective revocation
and complete cleanup on leave. Frontend DOM checks passed, including per-row close.

Edge also displayed the four real local-test connections concurrently; screenshot
and logs are retained under ignored `.local/ui-qa/`. That local test then completed
with normal cleanup. This is functional verification, not a new throughput test.
Remote release `20261027-201714` passed deployment health checks and remains active
and enabled in systemd. Both local review pages were started with existing
identities and left offline. Old clients require updating for the new directory
messages; old live data sessions are not migrated.

## Per-request local listener port (2026-10-26)

Local port selection moved from server setup to connection requests in the
browser, HTTP API and CLI (`request --local-port`, default 0). The requester
keeps that value locally through approval and opens the listener on it; the
coordinator and recipient receive no local-port field. Exact target-port and
protocol authorization remains unchanged. One active grant per process remains.

Validation: **54 tests passed in 60.70 seconds**, including an approved TCP
transfer on an explicitly selected local port and rejection of invalid local
ports. Isolated frontend checks passed. Edge verified the port field is editable
while online and appears under connection requests rather than server setup.
Both review daemons were restarted; Wi-Fi directory membership was restored and
the cellular client left offline. No coordinator change or redeployment is needed.

## Dashboard usability and Edge validation (2026-10-26)

Client and administrator frontends now use responsive cards, state-specific
guidance, explicit empty states, action feedback and expandable diagnostics.
The client summarizes exact peer-port consent and its actual forwarding address.
Admin access has unlock/lock controls and membership counters. Revocation asks
for confirmation; a late poll cannot restore a locked dashboard. API contracts
and authorization rules are unchanged.

The five device/bundle regression tests passed, JavaScript syntax checks passed,
and isolated DOM checks covered input validation, safe rendering, stable polling,
forwarding-address display, unlock/lock, revocation cancellation and late-poll
locking. The browser integration was repaired with hash verification of 838
files and a native handshake, then verified through real Edge automation.
Edge displayed the client at desktop and 390px viewport widths; settings expand
correctly. The remote admin page loaded real membership data after sign-in and
cleared the token and data on lock. No membership was approved or revoked in
browser validation. Screenshots and the repair report are in ignored `.local/ui-qa/`.

## Separate web frontend (2026-10-25)

Client/admin HTML, CSS and JavaScript now live in `overlay/web/`, separately
from Python HTTP API handlers. External assets allow removing CSP's inline
script/style permission. Server wheels/deployment archives and phone zipapps
include the relevant resources; the phone bundle excludes the admin frontend.

Validation: **54 tests passed in 64.03 seconds**, including frontend HTTP serving,
API authorization and resource reads from the phone zipapp. JavaScript syntax
checks and a wheel build passed; both frontends were verified inside the wheel.
Release `20261025-223643` was deployed and passed health checks. Its remote
`/admin`, JavaScript and CSS returned 200 over pinned HTTPS; an unauthenticated
admin API request returned 401. Both existing local review clients were restarted
offline and their page/assets/API checked. Edge was launched with both local
client URLs and the direct remote admin URL; no browser visual validation or
browser trust-store change is claimed. The self-signed certificate is not trusted
by the default system trust store. See [frontend documentation](../guides/WEB_FRONTEND.md).

## Two-network proxy deployment and validation (2026-10-25)

The remote coordinator was upgraded to release `20261025-221134`. Two native
Windows clients used separate Wi-Fi and Android-bound cellular exits. Admission
and recipient approval were exercised through the same APIs as the browser UI.
Four 57,952,254-byte HTTP video transfers (both directions, direct and relay)
matched SHA-256; all four UDP echo cases received 30/30 datagrams. See the
[test report](../testing/DUAL_NETWORK_PROXY_TEST.md) for rates, latency and limitations.

Directory sockets now honor interface binding and the cellular adapter. Local
control cookies use instance-specific names so multiple browser pages coexist.
After these fixes, **53 native Windows tests passed in 58.66 seconds**.
Three control URLs were launched in Edge and their HTTP listeners verified;
browser visual automation was unavailable. Temporary memberships and application
servers were cleaned up, the cellular helper stopped, and the control pages
remain running with clients offline. The deployed coordinator remains active.

## Port-scoped consent and retired permission cleanup (2026-10-25)

Connection requests now carry an immutable destination port and TCP/UDP protocol.
Only the requester creates a local forwarding listener; the recipient accepts
channels solely to the approved loopback scope. Existing channel replies remain
bidirectional. Both clients validate port/protocol/direction against local consent.
The proxy marker is P3. The static peer whitelist, offline admin CLI and production
authorization bypass were removed. Older registry peer lists are migration input
only and are stripped on approval/revocation writes.

New tests reject changed ports/protocols, reverse channel creation and changed
broker approvals, and verify required port input, directed TCP and separately
approved UDP forwarding. Registry migration and live direct/relay tests run with
valid consent records rather than an authorization bypass.

Validation: **53 native Windows tests passed in 63.70 seconds**. Compilation,
dependency, local Markdown-link and daemon/CLI smoke checks passed. The full
device fixture selects a port available for both TCP and UDP to avoid Windows
protocol-specific reserved-port failures. No remote deployment or new throughput
benchmark is claimed for this revision.

## Device onboarding and shared management API (2026-10-25)

The client now starts without an identity or server configuration. Its page and
`ctl` CLI share the HTTP API for identity generation/import, server setup, directory
join/leave, peer requests/decisions, mode and disconnect. The coordinator has a
separate authenticated `/admin` UI/API for enrollment and revocation. Named service
publication was removed; consent activates the configured loopback default port.

New integration tests drive both real HTTP APIs and the CLI client over TLS:
pending enrollment cannot discover peers, wrong fingerprints cannot be approved,
membership alone cannot open a pair, request expiry/rejection cannot create it,
and only recipient approval activates TCP/UDP forwarding. They also cover live
direct/relay switching, revocation and pending-applicant disconnect cleanup.
Historical verification sections below refer to earlier interfaces.

Validation: **51 native Windows tests passed**. After final request-cleanup
hardening, the three device-flow tests passed again. A separate real-process CLI
smoke test passed empty startup, local browser-session delivery, identity generation,
server setup and status without contacting the deployed server. Dependency checks,
local Markdown links and the deployment shell syntax check passed. No remote
deployment, phone deployment, new throughput benchmark or browser visual automation
is claimed for this change; running processes still require coordinated upgrades.

## Connection proxy refactor (2026-10-25)

The production client is now a persistent TCP/UDP service proxy with a loopback
browser control interface. File, echo and benchmark code is outside the runtime
under `scripts/diagnostics/`. Old results below describe earlier revisions.

The new tests exercise TCP half-close and concurrent channels under packet loss,
UDP endpoint isolation and empty/maximum datagrams, slow-consumer bounds, service
rejection, and an actual HTTP server with credential/Host/Origin validation.
A live TLS/UDP coordinator test forwards one TCP connection over direct, then
relay, then direct, and checks disconnect/reconnect. The phone bundle excludes
diagnostic applications. No new Internet throughput benchmark or remote deployment
is claimed for this refactor.

Validation: **49 native Windows tests passed**; the later focused proxy and phone
bundle checks also passed after final lifecycle/CLI cleanup. A separate CLI smoke
test launched the proxy on an ephemeral loopback port and retrieved its HTML page
and authenticated status. Dependency and Markdown-link checks passed. Browser
visual/interaction automation could not run because its installed runtime module
was missing; HTTP behavior was tested against a real Uvicorn listener.

## Physical phone video transfer (2026-10-24)

The current coordinator and QUIC phone client were deployed to the real server
and Pixel 10 Debian environment. An existing 57,952,254-byte phone video was sent
to native Windows, with reciprocal fingerprint pins and SHA-256 verification.
Same-Wi-Fi relay and cellular direct/relay measurements, along with a controlled
direct receive-failure experiment, are recorded in
[the current performance summary](../performance/PERFORMANCE_SUMMARY.md) and its archived
detail reports. Raw measurements and video copies remain in ignored `artifacts/`;
no production transport policy was changed.

## QUIC transport (2026-10-24, current)

The data plane now uses aioquic/TLS instead of Noise. Auto mode prefers an
authenticated direct connection and keeps an independent end-to-end relayed
connection warm. A bounded logical message window restores unacknowledged data
after path selection and suppresses cross-connection duplicates. Reliable streams
are the default; `--delivery datagram` retains unreliable delivery semantics.

- **36 native Windows tests passed in 36.35 seconds**, including real TLS/UDP,
  client/server identity validation, forged certificates/proofs, pin mismatch,
  packet loss, ordering, backpressure, failover, address change, QUIC key update,
  control-session/certificate renewal and revocation.
- Focused Linux/WSL cryptographic, socket and phone-bundle checks passed.
  Both virtual environments pass dependency checks; Noise is no longer installed
  or required. The phone archive relies on platform-native aioquic dependencies.
- The two-NAT namespace run passed: direct **160/160**, blocked relay **160/160**,
  failure/recovery **640/640**, and peer restart **236/236 submitted**. The restart
  test separately recorded 124 unavailable send opportunities. Evidence is in
  ignored `artifacts/20261024-180215/`; namespaces/forwarding state were cleaned.
- The matching small Windows comparison recovered **60/60 versus 50/60** under
  injected loss and **200/200 versus 141/200** during a direct-path outage. More
  packets, CPU and recovery latency were observed; this is not a claim of a
  general performance improvement. See [the comparison](../performance/TRANSPORT_COMPARISON.md).
- Two integration findings were corrected: startup heartbeat could race admission
  proof, and Windows ICMP port-unreachable could stop Python 3.12 UDP reception.
  Socket setup is shared by client/server. Mounted-filesystem overhead was reduced
  by caching data-plane approval checks for 100ms; control admission forces refresh.

Old transport classes, Noise dependency/bundling, and incompatible passive one-way
diagnostics were removed. Earlier source remains only in Git/local evidence
archives, not as a selectable runtime implementation. Remote server and phone
deployment was subsequently validated by the physical video experiment above.
Queues remain in-memory and are cleared on
pair replacement, revocation or control disconnection; this is not durable resume.

## Remove redundant pair HMAC (2026-10-24)

Peer packets now use Noise AEAD directly. The coordinator no longer creates or
distributes a pair HMAC key; clients and diagnostic tools no longer store or use
one. UDP registration and relay envelopes retain their client-server session
HMAC. These envelopes remain unencrypted, while relayed application contents
remain end-to-end encrypted.

Native Windows validation: **37 tests passed in 23.22 seconds**. Real TLS/UDP tests
check direct and relay exchanges, recovery, renewal, revocation, absence of a pair
key/MAC, and rejection of modified ciphertext inside a correctly authenticated
server envelope. Server tests also reject wrong destinations, stale epochs,
relayed probes and plaintext payload fields. No remote deployment or full WSL
namespace experiment was performed for this simplification.

## End-to-end encryption (2026-10-24)

The source now uses standard Noise KK through pinned `noiseprotocol` and
`cryptography` dependencies. Ed25519 identities sign session X25519 public keys;
only the endpoints derive directional data keys. Direct, relayed, probe and peer
diagnostic packets carry encrypted payloads. Optional independently confirmed
peer fingerprint pins reject coordinator identity substitution.

- **36 tests passed on native Windows** in 23.24 seconds. These include real
  loopback TLS and UDP, 900-byte encrypted echoes over both paths, revocation,
  direct-to-relay-to-direct recovery, same-epoch address changes and scheduled
  renewal through a fresh control session/Noise handshake.
- Cryptographic adapter tests reject modified ciphertext/headers, reflected
  packets, replays, stale-session ciphertext, forged high nonces, wrong identity
  bindings, pin mismatches, modified handshakes and premature plaintext sends.
  They also exercise lost/out-of-order messages and expiry.
- Tests inspect bytes submitted to the real UDP transport and reject AEAD
  tampering even when the packet is re-signed with the coordinator's routing key.
- **6 focused Linux/WSL checks passed** in 8.60 seconds for the Noise adapter and
  phone zipapp with native crypto dependencies. No namespace experiment or
  physical NAT measurement was repeated for this change.
- Phone deployment now provisions pinned platform-specific dependencies in
  `.venv-e2e`; application archives contain Python code, not foreign native modules.

The remote server and actual phone have not been upgraded or tested with this
version. Both require a coordinated upgrade; existing Ed25519 identities remain
usable. Earlier network measurements below precede encryption. Integration is
tested but has not had an independent security audit. See
[the encryption guide](../guides/ENCRYPTION.md) for protocol and trust boundaries.

## Independent device admission (2026-10-24)

The shared admission token has been replaced in the source by administrator-approved
Ed25519 identities, exact node-name binding, per-peer permissions, and independently
granted metrics access. The remote server and phone have not been upgraded as part
of this source change; the earlier physical-network results below used the old protocol.

Validation of the new implementation:

- `pytest -q -p no:cacheprovider`: **30 passed** on WSL Ubuntu 24.04.
- Real loopback TLS tests reject unapproved identities, wrong private keys, replayed
  proofs, unapproved peers, and unauthorized metrics. Approved clients exchange direct
  and relayed echoes; revocation rejects subsequent UDP and clears the active pair.
- Native Windows checks passed for OpenSSL discovery, key generation, signing, and
  verification. The isolated phone zipapp generates its own identity without native
  Python extensions.
- The full namespace experiment completed with the new authentication protocol:

| Scenario | Echoes / sent | Server relay packets | Path sequence |
| --- | ---: | ---: | --- |
| Direct | 160 / 160 | 0 | probing → direct |
| Blocked from the start | 160 / 160 | 320 | probing → relay |
| Failure and recovery | 592 / 640 | 398 | direct → relay → direct |
| Peer restart | 274 / 274 | not measured | direct → waiting → probing → direct |

The failure/recovery case lost 7.5% of sent messages during detection and switching;
the UDP protocol does not retransmit them. During peer restart, send opportunities
without a ready path are tracked separately from messages actually submitted.
Local evidence is retained in `artifacts/20261024-160542/` (excluded from Git).
Namespace cleanup completed; no lab namespaces, uplink, or forwarding-state marker
remained. These are controlled local tests, not new real-NAT traversal measurements.

See [the authorization guide](../guides/AUTHORIZATION.md) for approval and migration.

## Earlier physical-network evidence

Physical-network evidence (2026-10-22 local): see
`artifacts/20261021-230632-two-real-nats/README.md` and the "Two Real NATs"
section of `docs/testing/NAT_DIAGNOSIS.md`. The following sections are historical snapshots;
their artifact paths now refer to entries in `.local/archive/pre-two-real-nats.zip`.
Their deployment/hash/configuration statements describe the time of those tests,
not a fresh audit of the current installation. That earlier suite had 25 tests.

Verified on 2026-10-21 (Europe/Stockholm). The raw artifact directory names use UTC.
Source files in `overlay/` were compared by SHA-256 with the deployed release;
all hashes match. No existing proposal documents were modified.

## Subsequent Diagnosis: Router-Side Workaround Verified

See [NAT_DIAGNOSIS.md](../testing/NAT_DIAGNOSIS.md) for the capture evidence and controlled
no-rule / rule-active / rule-deleted experiment. An explicitly approved temporary
UDP 54001-54002 forward enabled 160/160 direct echoes with DMZ still off. Removing
the rule restored automatic relay on the exact same ports. The rule was cleaned
up, DMZ and UPnP remain off, and the forwarding table is empty. Native host-to-VM
direct communication also passed independently. Twelve focused tests now pass.

## Host Retest After DMZ Was Disabled

The user reported that DMZ had been enabled during the earlier host test and was
disabled before this retest. Router configuration was not independently inspected.
The same Windows native two-process test was repeated, using fresh UDP sockets
and actual public endpoints. No NAT simulation, firewall changes, or router
changes were performed by the test.

Evidence: `artifacts/20261021-185514-host/results.json` and
`artifacts/20261021-185619-host-dmz-off-long/results.json`.

| Scenario after DMZ disabled | Echoes / Sent | Direct echoes | Server relay datagrams |
| --- | ---: | ---: | ---: |
| Natural auto selection, 8-second send phase | 160 / 160 | 0 | 320 |
| Explicit peer-receive fault injection | 160 / 160 | 0 | 320 |
| Relay-only control | 160 / 160 | 0 | 320 |
| Natural auto selection, 30-second send phase | 600 / 600 | 0 | 1200 |

The first natural run established these mappings:
`192.0.2.13:52473 -> 198.51.100.5:52473` and
`192.0.2.13:52472 -> 198.51.100.5:52472`.
Registration succeeded, but the implementation did not confirm a direct path
within 5 seconds; it automatically selected relay after approximately 5.172 seconds.
Mean application RTT was 50.43 ms, with no loss among sent echoes.

The longer natural run used different ports, 51574 and 51573, and continued
through the periodic direct retry windows without selecting direct. All 600
echoes returned through the public server. No peer-receive drop was injected in
either natural run. This shows working real NAT mapping and automatic fallback
after DMZ was disabled; it does not by itself identify whether the router,
another firewall, or endpoint handling prevented direct confirmation.

## Earlier Host Test: DMZ Enabled per User Report

This is the user's intended local setup. Evidence:
`artifacts/20261021-185323-host/results.json` and the scenario subdirectories.
The clients ran natively on Windows using `.venv-win/Scripts/python.exe`, without
WSL, containers, network namespaces, or software NAT configuration.

For the natural direct run the server observed:

| Process | PID | Local UDP socket | Public endpoint observed by server |
| --- | ---: | --- | --- |
| A | 32468 | 192.0.2.13:50290 | 198.51.100.5:50290 |
| B | 7232 | 192.0.2.13:50291 | 198.51.100.5:50291 |

Both peers used exactly the public endpoint supplied for the other process,
with no localhost or private-address shortcut. These are two distinct mappings
through the shared real NAT exit; they are not two independent NAT devices.
The direct path exercises actual NAT hairpin behavior.

| Native Windows scenario | Setup | Echoes / Sent | Mean RTT | Server relay datagrams |
| --- | ---: | ---: | ---: | ---: |
| Natural automatic path (direct) | 0.172 s | 160 / 160 | 3.49 ms | 0 |
| Automatic fallback with explicit peer-receive fault injection | 5.141 s | 160 / 160 | 50.13 ms | 320 |
| Relay-only control | 0.140 s | 161 / 161 | 49.97 ms | 322 |

The fallback injection discards direct peer datagrams inside the client;
registration and relay still traverse the real NAT. It does not simulate NAT
mapping or claim that the physical router rejected the peer packets. No host
firewall rules, routing configuration, or router settings were modified.
Observed mapping histories, peer endpoints, PIDs, and CSV samples are preserved.

`pytest -q` after adding native-host support: 10 tests passed, including the new
authenticated observed-endpoint recording and peer-drop isolation tests.

Reproduce the native test from PowerShell:

```powershell
.\scripts\run-host.ps1
```

## Deployment

- Server: `198.51.100.1`, TCP 8443 (TLS coordination), UDP 3478 (registration/relay).
- Current release: `/opt/p2p-overlay/releases/20261021-191425`.
- systemd service: `p2p-overlay`, active, running as user `p2p-overlay`.
- Manual rollback from `183559` to `183301`, then back to `183559`, passed health checks.
- Deployed source files are root-owned and not group/world writable; TLS key mode is 640,
  root-owned with the service group. Client credentials remain in ignored `.local/`.

## Deterministic local NAT experiment

Evidence: `artifacts/20261021-183250/`, including `results.json`, client/server logs,
per-echo CSV files, and PCAP files for the first three scenarios.

All runs use 256-byte application payloads at 20 packets/second.

| Scenario | Setup | Echoes / Sent | Relay datagrams | Captured direct data frames |
| --- | ---: | ---: | ---: | ---: |
| Direct | 0.060 s | 160 / 160 | 0 | 320 |
| Initial direct path blocked, automatic fallback | 5.044 s | 160 / 160 | 320 | 0 |
| Direct failure and recovery | 0.262 s | 582 / 640 | 400 | 780 |

The live failure run records `direct -> relay -> direct`. Loss during detection
was 9.06% over the entire 32-second send interval. UDP packets sent while the old
path is failing are not retransmitted. Successful steady-path runs delivered
40,960 application bits/second, the configured offered load, not a saturation
bandwidth measurement. Local RTT includes process scheduling and brief startup
echo buffering; these low-load runs are not a capacity study.

The restart run records `direct -> waiting -> probing -> direct` with a new pair
epoch. It received all 306 sent echoes; 54 of 360 scheduled send slots were skipped
while the peer was absent or reconnecting. Loss calculations exclude these unsent
slots and expose them separately as `unavailable_slots`.

## Public-server experiment

Evidence: `artifacts/20261021-183804/`. The service instance remained unchanged
within each measurement, so relay counter deltas are valid.

| Scenario | Setup | Echoes / Sent | Mean application RTT | Relay datagrams |
| --- | ---: | ---: | ---: | ---: |
| Automatic path, direct succeeded | 0.364 s | 240 / 240 | 4.13 ms | 0 |
| Direct UDP blocked, automatic fallback | 5.150 s | 160 / 160 | 48.65 ms | 320 |
| Relay-only control | 0.149 s | 160 / 160 | 48.97 ms | 320 |

Both clients sit behind separate simulated NATs but share the same real Internet
exit. The public direct test therefore demonstrates this exit's NAT hairpin path,
not arbitrary traversal between geographically distinct networks. The forced
fallback test blocks non-server UDP at both simulated gateways, while preserving
UDP registration and TCP control traffic to the real server.

Earlier diagnostic runs were affected by service restarts during the server's
automatic package update activity. They remain in their artifact directories for
diagnosis but are not used for the table above. A server instance ID was added so
future experiments fail explicitly if counters span a restart. Clients were also
observed reconnecting and obtaining fresh sessions after restart.

## Authentication, Expiry, and Cleanup

`artifacts/remote-check.json` confirms:

- Pinned-certificate HTTPS health succeeds.
- The self-signed certificate is rejected without the pinned trust certificate.
- Metrics without a bearer token return HTTP 401.
- WebSocket registration with an invalid token returns HTTP 403.
- A silent authenticated session expires after the configured 20 seconds, closes
  with code 4000, increments the expiry counter, and disappears from online nodes.

`pytest -q`: 8 tests passed. Coverage includes packet tampering, invalid signatures,
replay/reordering windows, payload limits, expired relay peers, session cleanup,
bidirectional nonce confirmation, old epoch rejection, duplicate suppression
across direct/relay paths, and relay-only path selection. Shell syntax and Python
compilation checks also passed.

After experiments, no `ov-*` namespaces remain and host IPv4 forwarding is restored
to its original value, 0. The public systemd service remains running.

## Reproduce

From the project root in PowerShell:

```powershell
.\scripts\deploy.ps1
.\scripts\run-lab.ps1
.\scripts\run-lab.ps1 -Remote
wsl -- .venv/bin/python scripts/check_server.py
wsl -- .venv/bin/python -m pytest -q
```

The current version authenticates UDP payloads but does not encrypt them. It is an
application-layer experiment, without a TUN interface, traditional VPN, or DNS.

## Additional NAT Controls (2026-10-21)

See `docs/testing/NAT_DIAGNOSIS.md` for timestamped two-ended captures and eight one-forward
probe-order cases. Registration remained live (63/63 over 62.33 seconds without
any static forward), while new server source ports were blocked until contacted.
A-first hairpin cases used ports 1025-1028; B-first cases preserved the original
ports. All measured application relay deltas were zero. These diagnostics change
no production protocol behavior or deployment. Fifteen tests now pass.

The user explicitly requested retaining a single optional debug rule, UDP 54011
to `192.0.2.13:54011`. Other ports have no static forward; DMZ/UPnP remain off.
Temporary VM listeners, helpers, and capture processes were removed.

## Public Ethernet and Hotspot (2026-10-22 Local)

See `docs/testing/NAT_DIAGNOSIS.md` and `artifacts/20261021-220159-multi-interface/`.
Two auto runs completed 118/120 and 120/120 direct application echoes, with zero
server relays. Capture verifies both physical interfaces, selected UDP/TLS
egress and zero scoped loopback packets. A relay-only control returned 119/121
echoes and counted 240 server relay forwards. An earlier valid auto run naturally
fell back to relay after detecting a changed authenticated hotspot source.

`artifacts/20261021-220315-multi-interface-probe/` records a hotspot registered
endpoint :47162 and peer-observed :47152. Replying to the original endpoint
failed 0/20 even after contact; the authenticated actual endpoint delivered
20/20. This diagnostic is not production peer-reflexive implementation.

The Ethernet address is public, so this is public-host/mobile-NAT testing, not
two independent private NATs. No global routes/firewall/router changes or remote
redeployment occurred. The local client supports optional Windows interface
pinning for UDP and TLS. All test processes stopped; 24 tests pass.
