# Configuration and command-line reference

The installed command is `p2p-overlay`; `python -m overlay` is equivalent.
`--help`, each subcommand's `--help`, and `--version` work without starting a
daemon. Commands return JSON for automation where applicable. Invalid input exits
with code 2, failed `doctor` checks with code 1; successful commands exit 0.

## First use

```text
p2p-overlay config init --output .local/client.json
p2p-overlay config check --config .local/client.json
p2p-overlay doctor
p2p-overlay client --config .local/client.json
```

Initialization refuses to overwrite a file. It creates no identity, certificate
or connection permission. The default client template starts offline and can be
configured through the browser or `ctl`. Existing `.local/client.json` users can
skip initialization entirely. Checked-in templates live in [examples](../../examples).

For a coordinator:

```text
p2p-overlay config init --kind server --output .local/server.json
p2p-overlay config check --kind server --config .local/server.json
p2p-overlay config check --kind server --config .local/server.json --files
p2p-overlay server --config .local/server.json
```

Place the certificate/key beside this generated config before the file check or
startup; see [TLS setup](USAGE.md#server-first-run). Validation does not need an
existing membership registry/admin credential because runtime creates them.
Explicit server flags such as `--udp-port 4478` override configuration values.
The old `server --cert ... --key ... --registry ...` command remains supported.

## Client fields (schema_version 1)

| Field | Meaning / default |
| --- | --- |
| `schema_version` | `1`; opts into strict fields and config-relative file paths |
| `server` | Coordinator IPv4 address or hostname, without scheme/port; optional before setup |
| `control_port` / `udp_port` | Remote coordinator ports, 8443 / 3478 |
| `management_port` | Local loopback browser/HTTP API port, 8765; 0 chooses a free port |
| `control_token_file` | CLI credential path; default `CONFIG.control-token` |
| `node` / `identity` | Device name and Ed25519 private key path; UI may generate/load these |
| `ca` | CA/certificate path; omit to use system trust |
| `mode` | `auto`, `direct-only` or `relay-only` |
| `bind_host` / `bind_port` | Outgoing IPv4 bind address / local UDP port, `0.0.0.0` / 0 |
| `interface_index` | Optional Windows outgoing interface index |
| `peer_pins` | Map independently verified peer names to lowercase SHA-256 fingerprints |
| `peer_fingerprint` | Compatibility single-peer pin; prefer `peer_pins` for multiple peers |
| `delivery` / `drop_peer_udp` | Diagnostic options, `reliable` / false; drop is fault injection |

No global destination port belongs here: each connection request has its own
destination port/protocol and local forwarding port. The local management port
is not the remote control port and is not an application forwarding port.

```json
{
  "schema_version": 1,
  "server": "server.example",
  "control_port": 8443,
  "udp_port": 3478,
  "management_port": 8766,
  "mode": "auto",
  "node": "workstation",
  "identity": "identities/workstation.key",
  "ca": "server.crt"
}
```

This example requires preexisting identity/CA files. Use the minimal template
for browser onboarding. Do not place private-key text, tickets or token values
in the public config; credentials live in separate ignored files.

## Server fields

`host` is the listen IPv4 address (`0.0.0.0`); `control_port` and `udp_port` are
8443 and 3478. `ttl` is a finite positive session lifetime in seconds (20).
`cert`, `key` and `registry` are required at startup. `admin_token_file` defaults
to `REGISTRY.admin-token`. Generated server config explicitly uses `admin.token`.
The coordinator hostname advertised to clients is configured on clients.

## Paths and precedence

- Version-1 JSON file paths resolve relative to the config's directory.
- Unversioned legacy JSON keeps the existing working-directory path behavior;
  additional legacy diagnostic fields remain accepted. Known field types are checked.
- Explicit CLI file paths are relative to the process working directory.
- `P2P_OVERLAY_CONFIG` selects the default client config for both `client` and
  `ctl`; `--config` takes precedence. The default remains `.local/client.json`.
- `P2P_OVERLAY_SERVER_CONFIG` selects optional server config; `server --config`
  overrides it, and explicit server options override individual config values.
- `ctl --url` / `--token-file` override `P2P_OVERLAY_CONTROL_URL` /
  `P2P_OVERLAY_TOKEN_FILE`, then derive defaults from client config.

To operate a second local client without repeating its token filename:

```text
p2p-overlay client --config .local/second.json
p2p-overlay ctl --config .local/second.json status
p2p-overlay ctl --config .local/second.json request --peer phone --port 22 --local-port 5001
```

Set `management_port` to a different fixed port in that config. If you override
the daemon's port with `client --control-port`, or use management port 0, pass
the actual URL printed at startup to `ctl --url`. Runtime CLI overrides are not
silently persisted. `ctl --admin` continues to require the administrator credential
and explicit remote URL/CA; it does not load device configuration.

Config checks and doctor are read-only and do not print resolved configuration
values. Startup and onboarding can create credential/state files or save settings.
Do not add `schema_version` to legacy JSON before checking field names and moving
relative-path references to match the new meaning.
