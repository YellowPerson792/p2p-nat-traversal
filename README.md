# P2P NAT Traversal

P2P NAT traversal and encrypted TCP/UDP port forwarding with relay fallback.
Approved devices discover each other,
request access to a specific peer TCP/UDP port, and forward ordinary application
traffic after the recipient approves. Direct UDP is preferred; an end-to-end
encrypted relay provides fallback. File transfer belongs to external applications.

Originally developed for EP2120 Internetworking at KTH, this project exposes
approved services behind NAT through local forwarding ports. It uses UDP hole
punching when reachable and an encrypted relay when a direct path cannot be
established. NAT and firewall policies can prevent direct connections; the relay
provides the fallback path.

See the [documentation index](docs/README.md) for guides, architecture,
performance, network test records and the repository layout.

## Workflow

1. Start a client and open its local browser page. Generate an Ed25519 identity
   or load a key file, enter the HTTPS server address, and join.
2. The server administrator approves the proven public identity on `/admin`.
   Private keys stay with the device. Membership permits discovery and requests.
3. Choose an online device, destination port and protocol. The recipient sees
   **who requests which TCP/UDP port** and can accept or reject.
4. Approval activates a local forwarding port on the **requester only**. A request
   for phone TCP 22 forwards the requester's local port to phone `127.0.0.1:22`.
   Replies travel normally through the same connection; no reverse port access
   or access to another destination/protocol is implicitly granted.

```text
Browser or CLI -> same local HTTP API -> identity, membership, port request
Server admin browser or CLI -> same HTTPS API -> admission / revocation
Requester application -> localhost:5001 -> encrypted direct/relay -> recipient localhost:APPROVED_PORT
```

## Run

Requires Python 3.12+ and OpenSSL with Ed25519 support. On Windows the identity
helper can also find OpenSSL bundled with Git for Windows. From the repository root:

```powershell
python -m venv .venv-win
.\.venv-win\Scripts\python.exe -m pip install -r requirements.lock
.\.venv-win\Scripts\python.exe -m pip install --no-deps -e .
.\.venv-win\Scripts\python.exe -m overlay client
```

The examples below use `python` from that environment. On Linux/macOS use
`python3 -m venv .venv`, activate it, and run the same installation commands.
Start a client on **each** device; the coordinator is a separate process.
For certificates, firewall ports and initial startup, see
[server setup](docs/guides/USAGE.md#server-first-run).

With the environment selected:

```powershell
python -m overlay client
```

Open **http://127.0.0.1:8765**. The browser receives a local HttpOnly same-site
session; it does not ask for private-key text or a local management token.
Default state is `.local/client.json`. Choose a local listening port with each
connection request; the default 0 selects a free port. Joining needs no local port.

The CLI uses the same APIs:

```powershell
python -m overlay ctl identity-generate --node pc
python -m overlay ctl setup --server https://SERVER:8443 --server-ca server.crt
python -m overlay ctl join
python -m overlay ctl peers
python -m overlay ctl request --peer phone --port 22 --protocol tcp --local-port 5001
```

On the recipient, inspect `ctl status`, then use
`ctl respond --request REQUEST_ID --accept` (or `--reject`). After approval,
`ssh -p 5001 droid@127.0.0.1` uses the normal SSH protocol through the proxy.
`ctl` reads `CONFIG.control-token`; global `--url` and `--token-file` select a
different local or SSH-forwarded client. Server-admin operations use `ctl --admin`.
See [usage](docs/guides/USAGE.md), [API](docs/guides/API.md), and [authorization](docs/guides/AUTHORIZATION.md).

## Main files

| File | Responsibility |
| --- | --- |
| [device.py](overlay/device.py) | Local identity, directory lifecycle and per-port consent |
| [control.py](overlay/control.py), [cli.py](overlay/cli.py) | Browser and CLI through the same HTTP API |
| [directory.py](overlay/directory.py) | Membership, discovery and directional port-scoped requests |
| [admin_control.py](overlay/admin_control.py) | Server admission/revocation page and API |
| [proxy.py](overlay/proxy.py) | Approved-port TCP/UDP channels and backpressure |
| [client.py](overlay/client.py), [secure.py](overlay/secure.py) | Discovery, QUIC/TLS, path choice and recovery |
| [server.py](overlay/server.py), [identity.py](overlay/identity.py) | Coordinator, opaque relay, identity proofs and registry |

Each port connection is represented by [connection.py](overlay/connection.py).
Closing one instance leaves the others running; leaving/revocation ends all
instances involving that device.

Python asyncio drives I/O and timers, aioquic/cryptography provide QUIC/TLS, and
FastAPI/Uvicorn provide HTTP/WebSocket endpoints. SSH/ADB are management tools.

Browser code is separate in `overlay/web/client/` and `overlay/web/admin/`;
Python exposes the shared HTTP APIs. See [frontend layout and direct remote
administration](docs/guides/WEB_FRONTEND.md).

## Boundaries and tests

- One device identity and directory session manage up to 16 independent port
  connections/pending requests, including different peers and TCP/UDP ports.
  Each instance has its own consent, data sockets, one QUIC connection and listener.
- Receiver access is restricted to the approved loopback port. Incoming OPEN
  records must match its port, protocol and direction. TCP is bidirectional after
  connection establishment; this does not authorize reverse connection creation.
- Consent is bound to both live directory sessions. Leaving/revocation closes
  channels. In-session direct/relay switching preserves reliable delivery; there
  is no durable application resume.
- TLS authenticates the server's device-bound session certificate; an encrypted
  stream challenge verifies the client's approved device identity. No private
  TLS authentication hooks are used.
- TCP channels use independent QUIC streams and native ordering/recovery, with
  bounded 512 KiB receive credit per channel. See [multi-stream transport](docs/architecture/MULTISTREAM_TRANSPORT.md).
  UDP preserves boundaries, is unreliable, has an 881-byte payload limit
  and 60-second idle expiry. This is not TUN, SOCKS or arbitrary-host forwarding.
- Static `peers` permissions, offline `admin --peer` CLI and authorization bypass
  switches were removed. Old registry peer-list fields are ignored on read and
  removed on the next approval/revocation write; identities are preserved.
- The coordinator is trusted for discovery. Independent `peer_pins` remain needed
  to detect identity substitution; directory-delivered pins are not independent.

For a repeatable development environment, run `python scripts/bootstrap.py`.
It preserves existing `.local` files and keeps all tests/diagnostic tooling.
See [contributing](CONTRIBUTING.md) for dependency updates and package builds.
Alternatively install tests with `python -m pip install -e ".[test]"`, then run
`python -m pytest -q`. Tests cover admission, browser/CLI
API parity, port/protocol/direction enforcement, consent expiry/rejection, revocation,
TCP/UDP forwarding and direct/relay recovery. Low-level fixtures create valid
consent records; production authorization has no test bypass branch.

[Two-network proxy validation](docs/testing/DUAL_NETWORK_PROXY_TEST.md) records earlier
port approval, HTTP video transfers and UDP forwarding over Wi-Fi and cellular;
it predates the current multi-stream/allocation revision.
[Earlier performance results](docs/performance/PERFORMANCE_SUMMARY.md) are historical baselines,
not benchmarks of this interface. Server and clients need coordinated upgrades.

The compact UDP envelopes use `OD3` / `OR3` and omit the duplicate QUIC CID.
Deploy matching coordinator and client versions together; ALPN is `overlay-quic-v8`.

Relay packets resolve a unique [allocation ID](docs/architecture/RELAY_ALLOCATIONS.md) to the
exact admitted pair; SID remains in control/registration, and epoch is removed.

See [forwarding optimizations](docs/performance/PROXY_PERFORMANCE.md) for local measurements
and remaining limits, and [repository privacy](docs/operations/REPOSITORY_PRIVACY.md) before
publishing logs, captures, configuration or Git history.

## Configuration and installed CLI

`pip install .` installs `p2p-overlay` and `p2p-overlay-diagnostics`. There is no
frontend build step. Initialize a client only if its configuration does not exist:

```text
p2p-overlay config init --output .local/client.json
p2p-overlay doctor
p2p-overlay client --config .local/client.json
p2p-overlay ctl --config .local/client.json status
```

Server JSON configuration is optional; the existing flag-only server command
still works. New `schema_version: 1` configurations have validated fields and
config-relative file paths; legacy configs retain their existing path behavior.
See [configuration reference](docs/guides/CONFIGURATION.md) for templates, precedence,
environment variables and multiple local profiles. MIT licensed; see [LICENSE](LICENSE).

[Connection maintenance](docs/architecture/MAINTENANCE.md) uses separate control heartbeats
and event/deadline-driven QUIC scheduling, adaptive probes and bootstrap-only
empty NAT hints. Authorization still requires live server control sessions.
