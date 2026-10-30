# Development

Use Python 3.12+ and OpenSSL with Ed25519 support. Git for Windows supplies a
compatible OpenSSL that the identity helper discovers automatically. From a clone:

```text
python scripts/bootstrap.py
```

This creates or reuses `.venv-win` on Windows, `.venv` elsewhere, installs the
pinned runtime/test requirements and an editable package, then runs `doctor`.
It does not recreate existing venvs or alter `.local` configuration/identities.
Use `--venv PATH` for an isolated environment, `--runtime-only` to omit tests,
or `--unlocked` to test dependency constraints instead of pinned versions.

Use that environment's Python for all following commands. Activation is optional:
Windows uses `.venv-win/Scripts/python.exe`, Linux/macOS `.venv/bin/python`.

```text
python -m pytest -q
python -m overlay doctor
python -m scripts.check_repository
git diff --check
```

Tests generate temporary identities and run local coordinators; they do not need
production credentials. Full tests exercise sockets and timers, so run outside
a heavily loaded machine when investigating timing failures. Windows and Linux
CI jobs are defined in `.github/workflows/tests.yml`; adding a job is not evidence
that it has passed remotely.

## Dependencies and distributions

`pyproject.toml` declares reusable installation constraints. `requirements.lock`
pins the full runtime; `requirements-client.lock` retains the phone runtime list;
`requirements-dev.lock` adds pinned tests. Update and check both runtime lists
together. Locks pin versions, not artifact hashes or OS packages. The aioquic
1.7.0 pin is intentional: upgrading requires native-buffer adapter regressions.

Build a wheel and source distribution using an isolated build environment:

```text
python -m pip install ".[dev]"
python -m build
```

The wheel includes client/server code, browser resources, configuration commands,
and the diagnostic application package. The source distribution additionally
includes tests, examples, docs and platform scripts. Install a built wheel in a
new venv from a directory outside the checkout, then run `p2p-overlay doctor`,
`p2p-overlay config check` and `p2p-overlay-diagnostics --help`. This catches
missing resources that an editable install can conceal.

## Existing local workflows

`python -m overlay client`, `.local/client.json`, browser/HTTP APIs, `ctl`, and
offline `identity` remain. Unversioned configs retain working-directory-relative
paths and diagnostic extensions. New version-1 configs resolve file paths beside
the config and reject unknown keys. Migration is opt-in; no local config is
rewritten by bootstrap or config validation. See [configuration](docs/guides/CONFIGURATION.md).

Keep `tests/`, `scripts/diagnostics/`, `scripts/transport_experiment.py`,
`scripts/host_experiment.py`, lab scripts, phone/cellular tooling and benchmarks.
They are useful developer tools, even when not part of the normal user workflow.
Diagnostic applications still require valid membership and connection consent;
there is no production authorization bypass. Use `python -m scripts.diagnostics
--help` in a checkout or installed `p2p-overlay-diagnostics --help`.

`Device`, `Client`, `Proxy` and the HTTP endpoints remain available. For now the
documented CLI/HTTP API is the integration surface; Python internals and wire
formats are experimental. Examples of adapter boundaries and stream APIs remain
in [transport design](docs/architecture/MULTISTREAM_TRANSPORT.md). Protocol upgrades must
coordinate server and both clients.

## Changes and publication

Keep behavior changes covered by tests, especially port authorization, bounded
buffers, half-close, path switching and revocation. Describe benchmarks with the
software revision, path, endpoints and measurement boundary. Keep raw captures,
credentials and local reports ignored. See [privacy](docs/operations/REPOSITORY_PRIVACY.md)
for history findings that still matter before pushing the existing Git history.

New project contributions use the [MIT license](LICENSE). Third-party dependencies
retain their own licenses; bundled phone artifacts preserve upstream notices.
