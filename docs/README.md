# Documentation

Commands are run from the repository root unless stated otherwise. Start with
the [project README](../README.md) for installation and a short workflow.

## User guides

- [Usage and server setup](guides/USAGE.md)
- [Configuration and profiles](guides/CONFIGURATION.md)
- [CLI and HTTP API](guides/API.md)
- [Device approval and port authorization](guides/AUTHORIZATION.md)
- [Encryption and trust](guides/ENCRYPTION.md)
- [Browser UI and remote administration](guides/WEB_FRONTEND.md)

## Architecture

- [Streams, flow control and forwarding](architecture/MULTISTREAM_TRANSPORT.md)
- [Relay allocations and session lifecycle](architecture/RELAY_ALLOCATIONS.md)
- [Timers, heartbeat and connection maintenance](architecture/MAINTENANCE.md)

## Performance

- [Forwarding optimizations and remaining limits](performance/PROXY_PERFORMANCE.md)
- [Historical performance summary](performance/PERFORMANCE_SUMMARY.md)
- [Bulk transfer optimization](performance/BULK_TRANSFER_OPTIMIZATION.md)
- [Transport comparison](performance/TRANSPORT_COMPARISON.md)

Historical measurements describe their named revisions and environments; they
are not performance guarantees for the current release.

## Network test records

- [Two-network proxy validation](testing/DUAL_NETWORK_PROXY_TEST.md)
- [NAT diagnosis](testing/NAT_DIAGNOSIS.md)
- [Phone network routing](testing/PHONE_NETWORK_ROUTING.md)
- [Phone direct video retest](testing/WIRELESS_PHONE_VIDEO_RETEST.md)
- [Phone relay video retest](testing/WIRELESS_PHONE_RELAY_RETEST.md)
- [SCP dual-network benchmark (2026-10-30)](testing/SCP_DUAL_NETWORK_20261030.md)
- [Interactive SSH and loaded transfer test (2026-10-30)](testing/SSH_INTERACTIVE_20261030.md)

## Development and release

- [Contributing and reproducible dependencies](../CONTRIBUTING.md)
- [Verification record](operations/VERIFICATION.md)
- [Publication privacy and local state](operations/REPOSITORY_PRIVACY.md)
- [Archived performance reports](archive/performance-2026-10-25/README.md)

## Repository layout

| Path | Contents |
| --- | --- |
| `overlay/` | Reusable package, CLI, HTTP APIs and browser assets |
| `tests/` | Regression tests and local fixtures |
| `scripts/` | Bootstrap, deployment, diagnostics and experiments |
| `examples/` | Public configuration templates |
| `docs/` | Guides, design, reports and publication records |
| `.github/` | CI workflows |
| `.local/`, `artifacts/` | Ignored credentials, local state and raw evidence |

The root keeps the README, license, contributor entry point, Python packaging
metadata, Git settings and dependency locks. Virtual environments, caches and
build metadata are local generated directories and are not published.
