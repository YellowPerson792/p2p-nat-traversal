# P2P NAT Traversal

P2P NAT traversal and encrypted TCP/UDP port forwarding for reaching approved
services across private networks. The project establishes a direct peer path
when possible and falls back to an opaque relay when NAT or firewall policy
prevents direct traffic.

This is an application-layer forwarding system. It is not a full VPN, TUN
interface, SOCKS proxy, or arbitrary-host forwarding service.

## What It Provides

- Direct UDP hole punching with relay fallback.
- End-to-end encrypted QUIC connections between approved peers.
- TCP forwarding with independent ordered streams and bounded flow control.
- UDP forwarding that preserves datagram boundaries and enforces payload limits.
- Explicit approval for each destination port, protocol, direction, and session.
- Ed25519 device identities, server admission, peer authentication, and revocation.
- Browser and command-line control through the same local HTTP API.
- Multiple independent port connections without one connection replacing another.

## Example Uses

After the recipient approves a request, an application can use a local port as if
the destination service were local:

- SSH access to a development machine behind a home router.
- Access to an internal HTTP service, database, or metrics endpoint.
- Remote development tools that need a TCP service without exposing it publicly.
- UDP telemetry, game-server, or media protocols between approved hosts.
- Controlled lab experiments comparing direct paths and relay paths.

The recipient approves the exact service. A request for TCP port 22 does not grant
access to another port, create reverse access, or expose the recipient's whole
network.

## How It Works

```text
local application
        |
        v
requester's local proxy
        |
        |  QUIC/TLS, direct UDP when reachable
        |-------------------------------> peer proxy
        |                                  |
        |  opaque encrypted relay fallback |
        |----> coordinator relay ----------|
                                           v
                                  approved loopback service
```

Each device creates an Ed25519 identity and joins a coordinator over HTTPS and
WebSocket control channels. The coordinator authenticates membership, exchanges
peer endpoints, and records the live sessions. It does not receive application
payloads in plaintext.

For a connection request, the requester names a peer, destination port, protocol,
and local listening port. The recipient checks the request and returns an approval
or rejection. Once approved, both clients establish one QUIC peer connection. TCP
channels use separate QUIC streams; UDP uses authenticated datagrams. The clients
probe the direct path and switch to the coordinator's opaque relay when needed.

The coordinator forwards encrypted QUIC packets only. The peer that owns the
approved service is the component that opens the final loopback TCP or UDP socket.
Leaving, revocation, consent expiry, or control loss closes the associated
connections.

## Architecture

| Component | Role |
| --- | --- |
| `overlay/device.py` | Local identity, lifecycle, approval state, and per-port connections |
| `overlay/control.py`, `overlay/cli.py` | Browser and CLI control surfaces using the same HTTP API |
| `overlay/directory.py` | Membership, discovery, live sessions, and directional requests |
| `overlay/proxy.py` | Local TCP/UDP listeners, channels, backpressure, and cleanup |
| `overlay/client.py` | Peer discovery, path selection, direct probing, and relay use |
| `overlay/secure.py` | QUIC/TLS transport, identity proof, authenticated records, and recovery |
| `overlay/server.py` | HTTPS/WebSocket coordinator, membership, and opaque relay |
| `overlay/connection.py`, `overlay/streams.py` | Independent connection and stream state |
| `overlay/web/` | Browser client and administrator interfaces |

The data path is application forwarding rather than IP routing. A single approved
port owns its connection lifecycle; other approved ports have independent state.
The coordinator is trusted for discovery and admission, while peer identity proofs
and consent checks are enforced by both clients.

## Open-Source Software and Technologies

- Python 3.12+
- `asyncio` for concurrent sockets, timers, and lifecycle management
- `aioquic` for QUIC, TLS transport, streams, datagrams, and recovery
- `cryptography` and OpenSSL Ed25519 for identities and signatures
- FastAPI and Uvicorn for HTTPS, local control APIs, and administration
- WebSockets for coordinator control sessions
- Pytest for the regression suite
- GitHub Actions for Windows/Linux Python 3.12/3.13 CI

The project is released under the MIT License. Runtime and development versions
are declared in `pyproject.toml` and pinned lock files.

## Repository Layout

```text
overlay/        reusable package, CLI, APIs, transport, and browser assets
tests/          regression tests and protocol fixtures
scripts/        bootstrap, deployment, diagnostics, and experiments
examples/       public client/server configuration templates
docs/           guides, architecture notes, performance, tests, and archive
.github/        CI workflows
```

Start with the [documentation index](docs/README.md). It links to user guides,
configuration, API details, architecture, performance records, and maintenance
notes. Local identities, credentials, logs, captures, and build environments are
kept under ignored paths such as `.local/`, `artifacts/`, and virtual environments.

## Installation

Requirements: Python 3.12 or newer and OpenSSL with Ed25519 support.

For a reproducible development environment:

```powershell
python scripts/bootstrap.py
.\.venv-win\Scripts\python.exe -m pytest -q
```

On Linux or macOS, the bootstrap script uses `.venv` instead of `.venv-win`:

```bash
python3 scripts/bootstrap.py
.venv/bin/python -m pytest -q
```

The bootstrap script preserves existing `.local` configuration and identities.
It does not generate or overwrite credentials implicitly.

## Running a Client and Coordinator

Create a server configuration from the example and replace its certificate,
private-key, and registry paths with files for your deployment. Start the
coordinator, then start one client on each participating host:

```powershell
python -m overlay server --config .local/server.json
python -m overlay client --config .local/client.json
```

The client browser interface is available at `http://127.0.0.1:8765` by default.
The coordinator administrator interface uses the configured HTTPS endpoint.

Configuration templates can be created and checked without exposing values:

```powershell
p2p-overlay config init --kind client --output .local/client.json
p2p-overlay config init --kind server --output .local/server.json
p2p-overlay config check --config .local/client.json
p2p-overlay config check --kind server --config .local/server.json
p2p-overlay doctor
```

The existing module invocation remains supported:

```powershell
python -m overlay client
python -m overlay ctl peers
python -m overlay ctl request --peer peer-a --port 22 --protocol tcp --local-port 5001
python -m overlay ctl respond --request REQUEST_ID --accept
```

After approval, a normal client such as SSH connects to the requester's local
forwarding port:

```powershell
ssh -p 5001 user@127.0.0.1
```

See [usage](docs/guides/USAGE.md), [configuration](docs/guides/CONFIGURATION.md),
[API](docs/guides/API.md), and [authorization](docs/guides/AUTHORIZATION.md) for
server certificates, membership, control tokens, and deployment details.

## Tests and Diagnostics

```powershell
python -m pytest -q
python -m scripts.check_repository
python -m overlay doctor
python -m scripts.diagnostics --help
```

The tests cover identity and admission, browser/CLI API parity, port and protocol
enforcement, consent expiry, revocation, TCP/UDP forwarding, stream recovery,
direct/relay switching, and repository migration. Historical network measurements
are listed under `docs/testing/` and are not guarantees for every deployment.
