# Shared HTTP API and port-scoped authorization

Browser and `python -m overlay ctl` call the same endpoints. Device control binds
to `127.0.0.1:8765`. Visiting `/` grants an HttpOnly, SameSite=Strict browser cookie;
CLI calls use `Authorization: Bearer TOKEN` from `CONFIG.control-token`. Foreign
Host/Origin values are rejected. Server-admin APIs require a separate administrator
Bearer credential over verified HTTPS. Neither credential is an admission key.

Frontend HTML/CSS/JavaScript are separate files under `overlay/web/`; see
[web frontend](WEB_FRONTEND.md) for asset routes, hosting and direct remote access.

## Device API

| Method | Path | Body / behavior |
| --- | --- | --- |
| GET | `/` | Local browser page |
| GET | `/api/status` | Membership state, identity, peers, requests, `connections[]` (each with ID, port scope/listener and counters) |
| POST | `/api/identity/generate` | `{"node":"pc"}` |
| POST | `/api/identity/load` | `{"node":"pc","path":"LOCAL_KEY_PATH"}` or local import using `key_pem` |
| POST | `/api/setup` | `{"server":"https://host:8443"}`; optional `ca_pem` or `ca_path` |
| POST | `/api/join` | Join; may remain awaiting administrator approval |
| POST | `/api/leave` | End directory session and active consent |
| POST | `/api/requests` | `{"peer":"phone","port":22,"protocol":"tcp","local_port":5001}`; return 202 with `{queued: true, id: CONNECTION_ID}` |
| POST | `/api/requests/{id}` | `{"accept":true}` or false; recipient only |
| POST | `/api/disconnect` | Close all connections / pending requests, remain online |
| DELETE | `/api/connections/{id}` | Close just this connection or cancel this pending request |
| POST | `/api/mode` | `{"mode":"auto"}`, `direct-only` or `relay-only` |

`local_port` selects the requester's local listening port per request (0..65535,
default 0 for automatic allocation). It stays on the requester and is not part of
the server approval or recipient authorization. Setup no longer accepts `forward_port`.

`port` is mandatory and must be an integer in 1..65535. `protocol` is TCP by
default, or explicitly UDP. The recipient cannot change scope while accepting;
the decision is a boolean applied to the stored request. Status includes the
port, protocol and incoming/outgoing direction, so the same scope is visible in
browser and CLI. Setup has no device-wide target port or service catalog.

Unknown fields/malformed bodies return 422; invalid state/scope 400; missing
authentication 401; foreign Host/Origin 403. Errors use `{"detail": ...}`. Private
keys are neither sent to the coordinator nor returned by status. Configuration
and identity changes require leaving first. Configuration is atomically persisted.

## Server administrator API

| Method | Path | Body / behavior |
| --- | --- | --- |
| GET | `/admin` | Administrator page |
| GET | `/admin/api/status` | Pending public identities and admitted/revoked members |
| POST | `/admin/api/enrollments/{id}` | `{"accept":true,"fingerprint":"EXPECTED_SHA256"}` or `{"accept":false}` |
| DELETE | `/admin/api/members/{node}` | Revoke membership and invalidate directory/data sessions |

The local administrator credential file is printed by path at server startup.
Unknown applicants prove possession of their submitted key before appearing for
approval. Conflicting node/key bindings and public-key reuse are rejected.

## Enforcement

Only admitted identities enter `/directory` and discover others. A request records
requester, recipient, destination port, protocol and both current directory sessions.
Recipient acceptance creates a short-scope ticket for exactly that connection.
`/ws` always requires valid consent plus identity proof; there is no static-peer
or test-bypass branch. Relay traffic rechecks live membership and consent.

Each client also matches approval against its own initiated/accepted request,
including fingerprint, port, protocol and direction. Only the requester creates
a local forwarding listener. The recipient proxy accepts OPEN records only for
the approved loopback port/protocol; the requester rejects reverse OPEN records.
Normal reply traffic remains allowed. Changing the destination, adding a host
field, changing protocol or reusing ended consent cannot expand the grant.

Proxy records use marker `P3`; older device-wide/service-based formats are rejected.
Static `peers` fields are ignored when reading older registries and stripped on
the next approval/revocation write. New records store identity, enabled status
and optional read-only metrics privilege, not a peer whitelist. The offline
`admin --peer` command and `require_consent` switch are removed.

A device supports a combined total of 16 active connections and pending requests, shared
across all peers. Each approved instance supports up to 64 application channels.
The server also limits global pending requests to 64 and active consents to 256. TCP
receive credit is 512 KiB per channel. Each reliable channel uses independent
QUIC streams and native recovery within one shared connection per instance. UDP is
unreliable, limited to 881 payload bytes, with 60-second idle
association expiry. Requests expire in 60 seconds and pending enrollments in
five minutes. Leaving/revocation closes channels and discards consent. Independent
`peer_pins` remain necessary to detect coordinator identity substitution.

## Device and connection instances

The device owns one identity, membership, directory WebSocket and HTTP control
API. Each request gets a client-generated random connection ID. The server
validates ID uniqueness and echoes it through approval/cancellation. Approved
instances use separate consent tickets and independent transport clients. The
coordinator indexes data sessions by `(consent ticket, device)`, not just device
name, so two ports on the same pair cannot replace or receive each other's data.

`GET /api/status` now returns `connections: [...]`, replacing the singular
`connection` field. Peer records report `connections` and `available`, replacing
`busy`. A device can initiate and receive several requests at once. The local
port stays in the requester's state; the recipient still approves only its exact
target port, protocol and direction. No service catalog or port-range grant is added.

`ctl close --id ID` closes one instance or cancels one request; `ctl disconnect`
closes all. Mode changes apply to all current instances and the default for new
ones. Device leave/directory loss/revocation closes all affected instances. A
local listener conflict closes only that new instance, with an error containing
its ID. Clients and coordinator require coordinated upgrades for the new
request-ID and scoped-close directory messages. Old live sessions are not migrated.

For channel mapping, resource bounds, ALPN `overlay-quic-v8` and endpoint upgrade
requirements, see [multi-stream transport](../architecture/MULTISTREAM_TRANSPORT.md).

Per-instance status includes `streams` (active application channels),
`quic_streams` (outgoing data stream count), `quic_original_destination_cid`
(the original native QUIC identifier, retained for diagnostics) and `peer_path` (last authenticated announcement).
Direct/relay selection retains the same native QUIC connection and streams.

Wire compatibility: `OD3` / `OR3` route by relay allocation ID; no per-packet
SID, epoch or duplicate outer QUIC CID is sent. The
coordinator and both endpoints require a coordinated upgrade; old binary
envelopes are rejected. API connection IDs and authorization scopes are unchanged.

`relay_allocation_id` in per-instance status identifies the exact current pair
of server data sessions. It changes when either session is replaced and remains
stable on a carrier switch or UDP address update. It is distinct from the API
connection/consent ID. Peer data-control announcements carry `allocation_id` and
`side`; the former epoch field is removed. See [relay allocation IDs](../architecture/RELAY_ALLOCATIONS.md).
