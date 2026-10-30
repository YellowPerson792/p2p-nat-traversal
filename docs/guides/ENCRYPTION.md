# Encryption and consent

Connection consent is directional and scoped to one recipient loopback port and
TCP/UDP protocol. The requester may open channels and both sides may exchange
data in those channels; the same ticket does not authorize reverse port access.
The receiving proxy checks the exact scope in P3 OPEN records.

The current directory flow proves device-key possession before enrollment, and
the administrator approves the exact public identity. Peer data sessions also
require a session-scoped two-party consent ticket. The ticket is routing/connection
authorization, not an encryption key; QUIC data keys are still derived by the
endpoints. Clients enforce their own request/approval record before starting the
proxy. See [API](API.md) and [usage](USAGE.md). Independent peer_pins remain
necessary to detect coordinator identity substitution; a directory-provided pin
is not independent verification.

## Data-plane cryptography reference

# Authentication and encrypted transport

The data plane uses aioquic 1.7.0 (QUIC/TLS 1.3). Noise, its wire format and its
dependency have been removed. WSS approval and device Ed25519 identities remain.

## Trust and authentication

1. The client validates the coordinator's TLS certificate, then signs a fresh
   Ed25519 admission challenge binding its node, requested peer and purpose.
   The server checks administrator approval and issues a session ID/HMAC key.
2. Each device generates a fresh Ed25519 TLS key/certificate for that control
   session. Its long-term identity signs the certificate, node, peer, session ID
   and protocol suite. The coordinator verifies and distributes these bindings.
3. Each endpoint verifies its peer's binding. An optional independently confirmed
   `peer_fingerprint` pins the peer's long-term identity. Without pins, identity
   distribution still trusts the coordinator.
4. The lexicographically smaller node is the QUIC client. Normal TLS validates
   the server using the device-bound certificate as its trust anchor and
   `overlay-peer` as its expected name. The project verifies the signed binding,
   certificate validity and server-authentication purpose through public APIs.
5. After TLS completes, stream 0 carries HELLO / CHALLENGE / PROOF / READY. The
   server issues a fresh random challenge; the client signs it with its approved
   long-term Ed25519 identity. The proof binds both names, relay allocation ID, original
   destination CID, server-bound certificate hash and fresh client/server nonces.
   The server accepts application traffic only after verifying the proof; the
   client waits for READY. This supplies the client authentication that normal
   server-authenticated TLS does not provide. No TLS client certificate is needed.

Authentication uses aioquic's public stream and TLS configuration APIs. The
private mutual-TLS adapter has been removed; no TLS methods or state-machine
flags are replaced. There is no TLS resumption or 0-RTT. Independent identity
pins, administrator membership and exact port consent remain separate checks.
The read-only native-buffer occupancy adapter is still used for backpressure;
removing the TLS adapter does not remove that separate internal dependency.

The coordinator never receives peer TLS private keys or QUIC data keys and does
not terminate peer QUIC. Its own WSS TLS session is separate.

## One QUIC connection over direct and relay carriers

Each approved port instance uses one peer QUIC connection in all modes, plus
its two endpoint-to-coordinator WSS data-control sessions. Auto mode selects
direct when authenticated same-carrier probes confirm recent reachability,
otherwise relay. The freshness threshold is three seconds while active and
seven seconds while idle, with a grace period when activity resumes. Bootstrap packets may try both carriers. Empty UDP
hints only open NAT state; they never prove identity or reachability.

The adapter switches physical carriers beneath a stable logical QUIC address.
This is not native address migration or Multipath QUIC. All streams retain their
IDs, offsets, native ACK and retransmission state across a switch. There is no
custom message ACK, replay cache or cross-connection sequence number. Active
QUIC CIDs can rotate and are carried only inside QUIC packets. The outer
envelope does not duplicate them. A CID is public routing information, not a
credential for using the relay.

Stream 0 authenticates the client inside the encrypted QUIC connection. The
first unidirectional stream from each endpoint (client 2, server 3) carries path
announcements and abort notices. Each application channel uses a bound unidirectional stream
in each direction. Reliable payload records allow up to 16 KiB; native sender
buffers apply bounded backpressure. UDP uses typed QUIC DATAGRAM frames with
no reliability or ordering. Early DATAGRAMs are discarded until authentication;
reliable control/data streams that overtake READY are buffered within limits.

QUIC handles key updates in place about every nine minutes. Session certificates
last one day; renewal after 23 hours closes the session and application sockets.
Carrier switching shares RTT/congestion state rather than resetting it for a
new physical route. See [stream mapping and limitations](../architecture/MULTISTREAM_TRANSPORT.md).
On Windows, shared socket setup disables ICMP-to-UDP connection-reset reporting
so probing a closed endpoint does not stop the Proactor receive loop.

## Wire format and MTU

| Traffic | Format/protection |
| --- | --- |
| Coordination | WSS TLS; approved identity signatures |
| UDP registration/reply | JSON + session HMAC, without encryption |
| Direct peer | 19-byte routing header + opaque QUIC datagram |
| Relayed peer | 28-byte allocation/routing header + opaque QUIC + 32-byte HMAC |

QUIC datagrams are capped at 1200 bytes. Thus direct UDP payloads are at most 1219
bytes and relay UDP payloads at most 1260, excluding IP/UDP headers. This fits a
1500-byte IPv4 MTU, but is not a guarantee for smaller paths/tunnels. Node names,
session IDs, allocation IDs and packet timing are not secret. There is no pair HMAC,
double encryption or Base64 expansion for QUIC packets.

The relay resolves allocation ID + source side to exact live sessions, then
checks session HMAC, replay window, registered source and reciprocal permissions. It cannot authenticate the inner QUIC contents; only
the endpoint can. Client relay replies also have a receive replay window.
Data-plane permission checks cache the registry for at most 100ms; admission and
identity binding force fresh checks. This avoids blocking packet processing on
filesystem metadata operations. Revocation remains subject to control delivery.

## Boundaries and removed redundancy

- Control disconnection, revocation or a new relay allocation ID clears the QUIC connection and its queued
  data. Restart-safe resume and durable acknowledgements are not implemented.
- Replacing peer identities remains possible for a malicious coordinator unless
  both endpoints pin independently verified identity fingerprints.
- The server can observe membership/relay metadata and deny service. Members can
  disclose their own plaintext. Long-term identity keys have no automatic expiry.
- Noise handshakes, peer HMAC, legacy payload codecs and the old passive one-way
  probe tool are removed. QUIC replaces encryption, transport replay protection,
  loss recovery and packet congestion control. Session HMAC still protects
  client-server UDP. No additional peer-level recovery ACK is used.
- The integration is tested, not independently audited. Connection admission,
  frame sizes and logical windows are bounded; general DoS protection remains basic.

Code: [client.py](../../overlay/client.py), [secure.py](../../overlay/secure.py),
[server.py](../../overlay/server.py), [wire.py](../../overlay/wire.py).
Commands: [AUTHORIZATION.md](AUTHORIZATION.md), [USAGE.md](USAGE.md).

The current data-link ALPN is `overlay-quic-v8`, with `OD3` / `OR3` envelopes.
The coordinator and both peers must upgrade/restart together. Old binary
envelopes are rejected; session authorization and HMAC checks are retained.
The identity trust chain and port approvals are unchanged.

See [relay allocation IDs](../architecture/RELAY_ALLOCATIONS.md) for routing, lifecycle and the
separation between QUIC state and server authorization state.

Heartbeat and control renewal run independently from QUIC data timers. Probe
rates, registration and NAT hints adapt to activity; see [maintenance](../architecture/MAINTENANCE.md).
The authorization lifetime and control-disconnection policy are unchanged.
