"""Local management credentials; never device or group admission keys."""
import secrets
from pathlib import Path


def credential_file(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with path.open('x', encoding='utf-8') as stream:
            path.chmod(0o600)
            stream.write(secrets.token_hex(32))
    token = path.read_text().strip()
    if len(token) < 32 or not token.isascii():
        raise ValueError('Invalid local management credential')
    return token
