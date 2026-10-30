# Authorization model

There are two approvals, not a static pair whitelist:

1. **Membership:** the server administrator admits an exact device identity.
   Enrollment requires proof of its Ed25519 private key. This allows directory
   presence, discovery and requests; it does not grant application access.
2. **Port access:** A requests B's particular loopback port and TCP/UDP protocol.
   B explicitly accepts that request. The resulting permission is directional:
   A may initiate channels to that B port, and B may send replies in those channels.
   It does not let B initiate connections to A or permit another B port/protocol.

The server stores consent with both current directory sessions and exact scope.
Data-control authentication and UDP relay require that live consent. Clients
independently require matching local request/approval state, including peer
fingerprint, port, protocol and direction. The recipient proxy checks every OPEN
against the granted scope. Ending a session or revoking membership closes channels.
Independent identity authentication, QUIC encryption and these consent checks serve
different purposes and remain in place.

New membership records have `public_key`, `enabled` and optional `metrics` fields.
`metrics` is read-only diagnostics privilege, not permission to connect to peers.
The old static `peers` list is no longer enforced or written. Existing files are
read without that field, and approval/revocation writes strip it while preserving
identity bindings. There is no legacy authorization bypass or offline `admin` CLI.
The offline key-generation utility remains; routine admission and revocation use
the administrator page or the same API via `ctl --admin`.

The browser local session and CLI management token authorize local control only.
The separate server administrator token authorizes membership management over TLS.
Neither is shared among ordinary devices. Device private keys are never coordinator
credentials to be pasted into a page or sent to the coordinator.

The coordinator still supplies discovery identities. Optional independent peer
fingerprint pins detect substitution; accepting a directory-delivered fingerprint
alone does not remove coordinator trust. Existing legacy single-peer pins remain
as a fallback rather than being silently discarded.

One device identity can hold multiple independent port grants, including different
peers and initiation directions. Each needs its own recipient approval and ticket;
device membership alone never authorizes a second port. Closing one instance does
not close the others. A device is limited to 16 active instances plus pending requests.
Leaving or revocation ends all instances involving that device. Revocation
cannot erase data already delivered, or stop two malicious devices communicating
outside this system.

See [usage](USAGE.md), [API](API.md) and [encryption](ENCRYPTION.md).
