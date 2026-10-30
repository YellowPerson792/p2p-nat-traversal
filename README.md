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

Prebuilt directory packages for Windows x64 and Ubuntu 24.04+ Linux x64, plus a
Linux coordinator/server package, are published on the
[GitHub Releases page](https://github.com/YellowPerson792/p2p-nat-traversal/releases).
Client archives contain a self-contained directory; extract the whole archive
and run `p2p-overlay.exe` (or `start-client.cmd`) on Windows, or
`./p2p-overlay` on Linux. Python is not required for these binaries. The Linux
server archive includes an installer; review its `SERVER.txt` before running it.
Each release includes SHA256 checksums. Windows packages are not code-signed.

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

## Deploying a Coordinator Server

The coordinator is the public rendezvous and encrypted relay endpoint. It does
not terminate the peer-to-peer application encryption. For a small deployment,
use a Debian or Ubuntu host with a public IPv4 address, systemd, and an SSH
account that can run installation commands as `root`.
The helper currently logs in as `root` directly. Install Python 3.12+ and OpenSSL
on that host first; it installs `python3-venv` if needed but does not upgrade
the system Python. The machine running the helper needs Python, `ssh`, and `scp`.

Allow these inbound ports in the cloud firewall and host firewall:

| Port | Protocol | Purpose |
| ---: | --- | --- |
| 8443 | TCP | HTTPS, WebSocket control, and administrator API |
| 3478 | UDP | Endpoint registration and encrypted relay packets |

The deployment helper creates a dedicated `p2p-overlay` service account, an
isolated virtual environment, `/etc/p2p-overlay/` for certificates and admin
credentials, and `/var/lib/p2p-overlay/` for the live membership registry.

### Automated deployment

From the repository root, verify that SSH already reaches the server as `root`,
then run:

```bash
python scripts/deploy.py --host YOUR_SERVER_IPV4
```

The helper uploads a versioned release to `/opt/p2p-overlay/releases/`, installs
the locked runtime dependencies, enables `p2p-overlay.service`, waits for the
HTTPS health endpoint, and downloads the public server configuration into
`.local/remote/`. It does not upload local identities or client credentials.

The first run generates a self-signed certificate whose subject alternative name
contains the supplied IPv4 address. Copy the public certificate to each client
and configure it as the client's `ca`. For an Internet-facing installation,
replace this certificate with one issued for the server's stable DNS name or IP,
preserving the private key permissions and restarting the service afterward.

Check the service and administrator credential on the server:

```bash
sudo systemctl status p2p-overlay
sudo journalctl -u p2p-overlay -n 100 --no-pager
sudo cat /etc/p2p-overlay/admin.token
```

Open `https://YOUR_SERVER_IPV4:8443/admin` and supply the administrator credential.
A self-signed certificate also requires browser trust configuration, separate
from the client's CA setting. Approve each device identity after independently checking its fingerprint.
Membership approval permits discovery and connection requests; it does not approve
any destination port automatically.

The helper packages the current working files, including uncommitted source
changes; check `git status` and run tests before deploying. To deploy a newer
version, run the same command again. Releases are
kept separately and the previous healthy release is retained for rollback:

```bash
python scripts/deploy.py --host YOUR_SERVER_IPV4
python scripts/deploy.py --host YOUR_SERVER_IPV4 --rollback
```

The deployment scripts currently target Debian/Ubuntu-style systems with
`apt-get`, `dpkg`, `systemd`, `ss`, and `openssl`. Other distributions can run the
server manually or adapt `scripts/remote-install.sh`.

### Manual server startup

For a development or manually managed host, install the package and locked
runtime dependencies, create a server configuration, and run:

```bash
python3 scripts/bootstrap.py --runtime-only
./.venv/bin/python -m overlay config init --kind server --output .local/server.json
```

Edit `.local/server.json` to point to your TLS certificate and private key before
starting. Version-1 file paths are relative to the configuration directory. For a
development certificate, follow [server first run](docs/guides/USAGE.md#server-first-run).
Then run:

```bash
./.venv/bin/python -m overlay config check --kind server --config .local/server.json
./.venv/bin/python -m overlay server --config .local/server.json
```

The server configuration must reference an existing certificate and private key,
plus a writable registry path. Keep the private key, registry, and administrator token outside the
repository with restrictive permissions. A missing or empty registry rejects
clients until an administrator approves their identities.

Use a single server process: live sessions and relay allocations are held in
memory. A restart requires clients to reconnect, and multiple independent workers
do not share that state. Back up the live registry and credentials before upgrades.
Automatic certificate renewal, bandwidth quotas, and distributed failover are
not provided by these deployment scripts. Upgrade server and clients together
when changing wire-protocol versions.

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
