"""Read-only installation diagnostics; no connections or credential contents."""
import importlib.metadata
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys


def diagnose():
    checks = [{'name': 'python', 'ok': sys.version_info >= (3, 12),
               'version': '.'.join(map(str, sys.version_info[:3]))}]
    for distribution, module in [('fastapi', 'fastapi'), ('uvicorn', 'uvicorn'),
            ('websockets', 'websockets'), ('aioquic', 'aioquic'), ('cryptography', 'cryptography')]:
        try:
            version = importlib.metadata.version(distribution)
            ok = importlib.util.find_spec(module) is not None
            if distribution == 'aioquic':
                ok = ok and version == '1.7.0'
        except importlib.metadata.PackageNotFoundError:
            version, ok = None, False
        checks.append(dict(name=distribution, ok=ok, version=version))
    executable = shutil.which('openssl')
    if not executable and os.name == 'nt':
        candidate = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Git/usr/bin/openssl.exe'
        executable = str(candidate) if candidate.is_file() else None
    try:
        result = subprocess.run([executable, 'list', '-public-key-algorithms'], capture_output=True,
                                timeout=5, **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {})) if executable else None
        ok = result is not None and result.returncode == 0 and b'ED25519' in result.stdout.upper()
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    checks.append(dict(name='openssl-ed25519', ok=ok))
    return {'ok': all(check['ok'] for check in checks), 'checks': checks}
