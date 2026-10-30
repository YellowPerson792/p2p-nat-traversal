# One QUIC connection, multiple carriers and streams

Each approved port instance owns one end-to-end QUIC connection. Direct UDP and
server relay are physical carriers for that same connection, not separate TLS
sessions. Separately approved ports still own independent connections and consent.

## Stream responsibilities

| Stream | Responsibility |
| --- | --- |
| TLS CRYPTO frames | Server authentication, encryption and handshake integrity |
| Bidirectional stream 0 | Client identity challenge, signed proof and READY |
| Client uni stream 2 / server uni stream 3 | Path state and channel abort notices |
| One unidirectional stream per channel and direction | BIND followed by application records, then FIN |
| QUIC DATAGRAM | Unreliable application UDP and short carrier reachability probes |

Each peer reserves its first uni stream for control after TLS completion;
application data streams start at client stream 6 / server stream 7. Stream 0
proves the client's approved device identity without private TLS hooks. The
server is authenticated by TLS and its signed certificate binding; see
[authentication and trust](../guides/ENCRYPTION.md). A pair of data streams represents a
bidirectional application channel. A 16-byte
application channel key binds the directions and must match proxy P3 records.
It is not a second packet sequence number or a replacement QUIC connection ID.
The forwarding layer allocates channels; the transport allocates native streams.
`streams.py` stores only channel lifecycle, with no ACKs or payload cache.

The API remains `open_stream(key)`, `await send_reliable(payload, stream=key)`,
`close_stream(key)` and `on_stream_data(key, payload, path)`. An unnamed stream
uses the default channel and the existing `on_data(payload, path)` callback.

## Connection identity and switching

The envelopes contain no duplicate QUIC CID. Direct packets contain only a
version marker, allocation ID and opaque QUIC bytes; relay packets additionally
carry a one-byte source side, replay sequence and session HMAC. The server routes
directly by allocation ID to the exact pair of admitted sessions; SID is used
only for control-session registration. See [allocation lifecycle](RELAY_ALLOCATIONS.md). QUIC itself identifies and authenticates packets using native
active CIDs, which can rotate normally across carriers. The original destination
CID remains available locally for diagnostics and the identity-proof context;
it is not a secret or a relay credential.

In auto mode, initial handshake packets can use both carriers, avoiding a relay
failure blocking an otherwise working direct route. Duplicate bootstrap packets
stop after authenticated path control arrives. There is only one QUIC handshake
state, one stream offset space per direction and one loss-recovery controller.

Authenticated nonce probes run every second on an active selected carrier,
every three seconds when idle and every five seconds on a healthy backup relay.
Failed backup direct probes back off. Only a timely
reply on the same physical carrier confirms reachability. Empty outer UDP hints
merely open NAT mappings and stop after authentication. Direct is preferred
while a confirmation is fresh (three seconds active, seven seconds idle, with
an idle-resume grace period); otherwise auto selects relay. Nonce probes also
check liveness, replacing the redundant standalone periodic QUIC PING. Each control stream reports its endpoint's choice; receiving the announcement
alone does not validate a path, and switches do not require simultaneous approval.
Forced modes accept only their selected carrier.

Changing carriers preserves the exact `QuicConnection`, active stream IDs,
stream offsets, queued data and FIN state. QUIC ACKs, duplicate detection and
loss recovery handle lost and late packets. There are no overlay message
sequence numbers, cumulative data ACKs, reordering maps or replay caches.
A normal close queues FIN immediately after its bytes; QUIC retransmits both.
An abort resets/stops only its channel and sends a small notice for cases where
RESET reaches the receiver before the channel's BIND record.

## Deliberate implementation boundary

This is **carrier switching beneath QUIC**, not native RFC 9000 address migration
or Multipath QUIC. The adapter presents a stable logical peer address to aioquic.
This supports independently moving P2P endpoints without modifying aioquic's
private path-selection state or violating client/server address checks.

Consequently, switching does not create independent RTT/congestion state or
reset the congestion window for a new physical route. RTT estimates can mix
paths. Recovery after a sudden failure includes the active/idle detection
window plus native loss recovery; backup direct retries also have backoff. No guaranteed switching latency, strict stream
priority or new throughput improvement is claimed. Native path validation and
per-path congestion control would require a different integration.

Path probes are best effort QUIC DATAGRAMs. Their queued route survives pacing;
only the probe packet is steered to its candidate carrier, while the rest of a
bulk batch uses the selected path. A probe can share a packet with application
bytes. Probes and acknowledgements consume the shared congestion budget.

## Bounds, backpressure and lifetime

At most 64 application channels plus one generic channel are active per port
instance. Native QUIC send buffers are bounded at 512 KiB per data direction and
8 MiB total for application admission, with 64 KiB reserved control headroom.
Parser buffers and probe queues are separately bounded; application DATAGRAM
queue admission stops at 64 queued frames. Proxy TCP receive credit remains
512 KiB per channel to prevent a slow local socket from accumulating data.

The pinned aioquic 1.7.0 low-level API exposes no `drain()` or send-buffer
occupancy. A small read-only adapter in `secure.py` inspects native sender
buffers and DATAGRAM queue length. It does not mutate library internals or copy
payloads into another recovery layer. Upgrading aioquic requires adapter tests.
`pending_count` now counts data send buffers with unacknowledged bytes, not
application messages; `pending_bytes` includes native control/data buffers.
Status exposes outgoing data-stream count, original destination CID, relay allocation ID and peer
carrier announcement.

Closed-channel IDs are bounded to 4096 per allocation. Once all channels finish, the
data-control session renews to reclaim tombstones. Control disconnect, approval
revocation, certificate renewal, QUIC expiry or a new allocation closes application
sockets; there is no restart-safe or durable resume. Application streams and
controls are gated by client identity authentication and bounded buffers.
Independent control/data streams that overtake READY wait for authentication.
Unreliable DATAGRAMs arriving before authentication are discarded. TLS completion
alone does not authorize client application traffic.

## Compatibility and verification

ALPN is `overlay-quic-v8`; direct and relay envelope markers are `OD3` / `OR3`.
The coordinator and both endpoints must upgrade/restart together. Older binary
envelopes are rejected without fallback; signed identity bindings are unchanged.
Direct framing is 19 bytes; relay framing is 28 bytes plus 32 bytes of HMAC.
Relay saves a further 15 bytes versus OR2 by replacing SID + epoch with allocation
ID + source side. Maximum UDP payloads are 1219 / 1260 bytes.
Authorization, registered-source checks, allocation validation and outer replay
protection remain in place; native QUIC CIDs do not authorize relay use.

Tests exercise encrypted loss with independent streams, original-CID continuity,
CID rotation, carrier failure/recovery, asymmetric choices, old-packet duplicates,
lost FINs, native-buffer backpressure, delayed client-authentication READY, TCP
half-close and actual loopback TCP/UDP forwarding through the coordinator. These are correctness
checks, not new mobile-network throughput or latency measurements.

The experimental mutual-TLS adapter and its private hooks have been removed.
Client identity signatures run inside encrypted public QUIC streams. Native
buffer occupancy still uses a read-only version-dependent adapter; upgrading
its pinned aioquic dependency requires backpressure regression tests.

See [maintenance scheduling](MAINTENANCE.md) for probe rates, control/data task
isolation, deadline wakeups and the unchanged server-dependency policy.
