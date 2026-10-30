# Port-scoped connections: browser and CLI

Install the runtime as described in [README](../../README.md#installation). Run commands from
the repository root with the virtual environment's Python. Addresses in this
guide and historical reports are anonymized examples, not live service endpoints.
For generated configuration files, installed commands and environment defaults,
see [configuration reference](CONFIGURATION.md). Existing configs need no migration.

## Server first run

Run the coordinator on a host reachable by both devices. Allow inbound TCP 8443
for HTTPS/WebSocket and UDP 3478 for registration/relay. Replace `SERVER` with
that host's address; its TLS certificate must cover that exact hostname or IP.
The optional SSH/systemd deployment helper requires an explicit target:

```powershell
python scripts/deploy.py --host YOUR_SERVER_IPV4
```

That helper deploys as root over SSH. For a manual test setup, create `.local`,
then generate a certificate using OpenSSL (replace the example IP in CN and SAN):

```powershell
New-Item -ItemType Directory -Force .local | Out-Null
openssl req -x509 -newkey ed25519 -nodes -days 365 -keyout .local/server.key -out .local/server.crt -subj "/CN=203.0.113.10" -addext "subjectAltName=IP:203.0.113.10"
python -m overlay server --cert .local/server.crt --key .local/server.key --registry .local/members.json
```

Use a matching trusted certificate for normal deployment. For the test
certificate, securely copy **only** `server.crt` to clients and select it as
their server CA. The server key and administrator credential stay on the server.
Browser trust is configured separately; do not disable TLS verification.
A missing registry starts with no approved devices; clients can submit enrollment
requests for review. Keep the server running while clients join.

## Client setup

```powershell
python -m overlay client --config .local/client.json --control-port 8765
```

Open `http://127.0.0.1:8765`. Generate an identity or choose an existing Ed25519
private key file. Enter `https://SERVER:8443`, optionally choose the project's
server CA certificate. Save settings and Join; no local forwarding port is
needed at this stage. Leave before changing identity or configuration.

The browser uses a local HttpOnly, SameSite=Strict session. The CLI reads the local
management credential from `CONFIG.control-token`. Neither is a device private
key or shared admission secret. Key import is into the local backend only; it
never sends a private key to the coordinator. Settings and identity selection
persist, while connection consent does not.

## Membership approval

```bash
python -m overlay server --cert server.crt --key server.key --registry state/members.json
```

Open `https://SERVER:8443/admin` and use the administrator credential whose file
path is printed at startup (`REGISTRY.admin-token` by default). Approve the proven
device public identity, verifying the fingerprint independently where needed.
Membership grants discovery/request capability, not access to other devices.

Use `https://SERVER:8443/admin`. A self-signed certificate requires browser trust
configuration independent of the Python client's CA setting. The previous localhost admin view was a test helper;
see [direct remote administration](WEB_FRONTEND.md#direct-remote-administration).

Systemd deployment uses `/var/lib/p2p-overlay/members.json` as writable state and
keeps TLS/admin secrets under `/etc/p2p-overlay/`. It preserves the previous registry
snapshot and existing credentials. Updating source alone does not restart a
deployed server. TLS certificate verification is always enabled.

## Request a particular port

In Available devices, enter the peer's destination port and select **TCP or UDP**,
choose a **local forwarding port** (0 selects a free port), then request the
chosen device. The local port belongs to this request and is not sent to the server. The recipient sees the requester, fingerprint,
protocol and destination port. Approve/reject applies to that immutable request.

For example, A requests B's TCP 22. Approval creates:

```text
A application -> A 127.0.0.1:5001 -> encrypted connection -> B 127.0.0.1:22
```

B creates no local forwarding listener. SSH traffic and replies are bidirectional
within this channel, but B cannot open connections to A's ports under A's request.
Access to B's UDP 22 or TCP 80 requires a different approval. All recipient targets
are loopback; the request cannot supply an arbitrary remote host.

A device can keep up to 16 approved connections and pending requests in total,
across the same or different peers. Each row approves one target port/protocol
and may carry multiple application channels. Repeat Request access to add more.
Use Close this connection or Cancel this request on a row to end only that
instance. Disconnect all ends all instances but keeps membership online; Leave
also ends directory membership. Directory loss or revocation ends all affected
instances. Requests expire after 60 seconds.

Two clients on one computer need distinct HTTP and local forwarding ports. Choose
forwarding port 0 to allocate one automatically and read it from connection status.
Do not choose a forwarding port already used by an application on the same host.

## CLI: the same APIs

Start the daemon first. Global options precede the operation:

```powershell
python -m overlay ctl identity-generate --node pc
python -m overlay ctl identity-load --node pc --key D:/keys/pc.key
python -m overlay ctl setup --server https://SERVER:8443 --server-ca server.crt
python -m overlay ctl join
python -m overlay ctl peers
python -m overlay ctl request --peer phone --port 22 --protocol tcp --local-port 5001
python -m overlay ctl status
```

On the recipient, select its HTTP port and local management credential:

```powershell
python -m overlay ctl --url http://127.0.0.1:8766 --token-file .local/phone.json.control-token status
python -m overlay ctl --url http://127.0.0.1:8766 --token-file .local/phone.json.control-token respond --request REQUEST_ID --accept
```

Use `--reject` to decline. `--port` is required, and `--protocol` defaults to TCP.
For UDP, request the same explicit port with `--protocol udp`. Use `mode auto`,
`mode direct-only`, `mode relay-only`, `close --id CONNECTION_ID`, `disconnect`
and `leave` for lifecycle control. `request` returns the new connection ID;
`status` lists each active instance in `connections`.
Request/respond return queued results; status reports the directory's outcome.

Within a port connection, concurrent TCP application connections use independent
logical/QUIC streams. Closing one application connection leaves the others alive.
The current transport requires `overlay-quic-v8` and compact `OD3` / `OR3`
envelopes. Upgrade/restart the coordinator and both endpoints together. See [stream mapping and bounds](../architecture/MULTISTREAM_TRANSPORT.md).

Administrator CLI operations use the same API as the server page:

```powershell
python -m overlay ctl --admin --url https://SERVER:8443 --ca server.crt --token-file admin.token status
python -m overlay ctl --admin --url https://SERVER:8443 --ca server.crt --token-file admin.token enrollment --request REQUEST_ID --accept --fingerprint VERIFIED_FINGERPRINT
python -m overlay ctl --admin --url https://SERVER:8443 --ca server.crt --token-file admin.token revoke --node phone
```

The old offline `admin` command and its static `--peer` permissions no longer exist.
`identity` remains an offline key-generation utility. Routine management is through
`ctl` or the pages. Windows file privacy depends on profile/workspace ACLs.

## Phone and compatibility

The client bundle includes the page/API and CLI, excluding server/admin modules.
For PC access to the phone's loopback page, use a trusted SSH forward:

```powershell
ssh -N -L 8766:127.0.0.1:8765 -p 2222 droid@127.0.0.1
```

Supply the existing SSH identity/host-key options where required. The CLI can use
the forwarded URL with the phone management credential. Never expose the local
control listener to the LAN. Rebuild and upgrade server and clients together;
the proxy now uses `P3` records with explicit port/protocol/direction checks.

Historical scripts/reports are not the operational permission model. See
[API](API.md), [authorization](AUTHORIZATION.md), and the
[performance summary](../performance/PERFORMANCE_SUMMARY.md).
