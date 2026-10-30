# Repository publication and local state

Run from the repository root before staging or publishing:

```powershell
python -m scripts.check_repository
python -m scripts.check_repository --history
python -m pytest -q
git diff --check
```

The first audit checks tracked and non-ignored untracked working files, excluding
deleted paths. It reports only filenames, line numbers and rule names, never
matching values. The history option also checks reachable HEAD blobs. A nonzero
exit means a finding needs review. This heuristic is not a complete secret scan:
encoded credentials, arbitrary JSON configuration fields, author metadata, tags,
other branches and remote-only objects require separate review. Run it again
after changing the publishable file set.

## Files that stay local

- `.local/`: device identities, management/admin credentials, configurations,
  deployment packages and recoverable cleanup backups.
- `artifacts/`: logs, packet captures, received files and raw experiment evidence.
- Virtual environments, build output, caches and attachment downloads.
- Private keys, credential files, approval requests and membership registries.

Ignore rules prevent ordinary accidental additions; they do not protect files
already tracked or undo prior publication. Never use `git add -f` for these files.
If a real credential was published, revoke/rotate it in addition to removing it.

Public examples use loopback, lab-only topology or reserved documentation
addresses. Real measured endpoint addresses have been replaced consistently;
the measurement results remain historical observations, not tests of the example
addresses. Use your own host, certificate and peer identities when deploying.

## Cleanup on 2026-10-30

Three old proposal documents and eight obsolete README/API/usage documents were
removed from the publishable tree. Their exact contents and the pre-cleanup
tracked files were archived under `.local/release-prep/` with a SHA-256 manifest;
the ZIP contents were verified before deletion. Historical performance reports
remain, clearly separated from current operating instructions.

The current working-file audit passes. The configured remote URL has no detected
embedded credentials. Git author/committer identity metadata remains in history;
its local audit is saved under `.local/release-prep/` without printing identities.
Reachable Git history still contains
previous network addresses, personal filesystem paths and three binary proposal
files requiring metadata/content review. No private-key or credential-literal
rule fired in that history audit. An ordinary push can include these old
versions even when the working tree is clean. History has not been rewritten.

A sanitized source ZIP is prepared under `.local/release-prep/` for review or a
fresh repository import. It deliberately excludes Git history and ignored local
state. Publishing the existing history requires a separate history cleanup;
publishing a fresh snapshot does not remove content from an existing remote.
