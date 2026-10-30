# Relay allocation IDs

An allocation is one server record binding exactly two admitted data sessions
for one approved port connection. The server generates a random 128-bit
`allocation_id` and tells both endpoints their ID and side (0 or 1) over WSS.
The ID is public routing information, not a bearer credential. Side numbering is
independent of QUIC client/server roles or the port-request direction.

## Identifiers and responsibilities

| Identifier | Scope and purpose |
| --- | --- |
| Device identity / node | Long-lived approved public identity |
| Consent / connection ID | Approved peer, target port, protocol and direction |
| SID | One endpoint's server control session; used for UDP registration and certificate binding |
| Allocation ID | Exact pair of currently admitted data sessions; used to route relay traffic |
| QUIC CID | Native end-to-end QUIC packet routing, stream continuity and CID rotation |

QUIC authenticates the peer and encrypts application traffic. The relay neither
terminates that connection nor has its keys. A native CID does not tell the relay
which membership/port grant permits forwarding, which registered UDP endpoint
is the destination or whether a control session was revoked. Checking that state
is the allocation service's responsibility. The server does not implement another
stream/reliability layer.

## Routing and lifecycle

`Coordinator.allocations` maps allocation IDs directly to a pair of Session
objects. Each Session points to its current allocation; there is no epoch field
or separate pair-by-SID routing table. The allocation uses the exact sessions,
not a lookup of the latest device with the same name.

For each relay request the server resolves the allocation and source side,
verifies the sender's session HMAC and replay sequence, checks the registered
source and both sessions' live authorization, then re-encapsulates the opaque
QUIC bytes with the recipient's HMAC key. The forwarded source side is preserved;
the recipient checks allocation ID and opposite-side origin before accepting
its server replay sequence. Knowing an ID or choosing another side is not enough
to use the relay.

Removing, expiring or replacing either session invalidates the allocation and
clears both endpoint references. A new pair gets a fresh ID even if one endpoint
keeps its SID. Late traffic for the old ID is rejected before it can be routed
to the replacement peer. Revoked or expired sessions cannot forward even before
background cleanup removes the record. An authenticated UDP address update
changes the endpoint in the same Session, preserving allocation and QUIC state.

The same ID scopes direct packets, the client identity proof and local channel
lifetime. A replacement ID closes old application channels and creates a fresh
QUIC connection. Carrier switching and CID rotation retain the ID. In direct-only
mode the record still identifies the authorized connection, but no application
traffic needs to traverse the relay.

## Wire format

| Traffic | Fields | Fixed overhead |
| --- | --- | --- |
| Direct / NAT hint | `OD3`, allocation ID, QUIC bytes (empty for hint) | 19 bytes |
| Relay | `OR3`, allocation ID, source side, sequence, QUIC bytes, HMAC | 28-byte header + 32-byte HMAC |
| UDP registration | SID, sequence, registration fields, HMAC | Existing JSON format |

Relay data no longer carries both SID and epoch. A one-byte side selects the
source Session and its HMAC key without trying two keys. Compared with OD2/OR2,
direct packet size is unchanged and each relay datagram saves 15 bytes. With the
1200-byte QUIC cap, maximum UDP payloads are 1219 and 1260 bytes, excluding IP/UDP
headers. QUIC CIDs occur only inside the encrypted transport's native packets.

ALPN is `overlay-quic-v8`; the coordinator and both endpoints must upgrade and
restart together. Old binary envelopes and peer announcements have no fallback.
Membership keys and approvals do not need to be regenerated.
