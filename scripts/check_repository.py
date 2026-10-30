"""Check publishable files without printing matching secrets; optionally scan history."""
import argparse
import ipaddress
import json
from pathlib import Path
import re
import subprocess


RULES = {
    'private-key': re.compile(r'-----BEGIN (?:OPENSSH |RSA |EC |ENCRYPTED )?PRIVATE KEY-----'),
    'personal-path': re.compile(r'[A-Za-z]:[\\/](?:Users|KTH)[\\/]|/home/(?!user(?:/|\b)|USER(?:/|\b))[A-Za-z0-9_.-]+/'),
    'credential-literal': re.compile(
        r'''(?i)(?:token|password|secret|api_key)\s*[:=]\s*["']([A-Za-z0-9_+/=-]{24,})["']'''),
}
IP = re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b')
PUBLIC_EXAMPLES = {'1.1.1.1', '1.0.0.1', '8.8.8.8', '8.8.4.4'}
PRIVATE_NAMES = re.compile(r'(?:\.(?:key|pem|p12|pfx|token|control-token|admin-token|request\.json)$|(?:^|/)(?:members|server|client)\.json$)')


def git(*args):
    return subprocess.check_output(['git', *args])


def inspect(name, data):
    try:
        text = data.decode('utf-8')
    except UnicodeError:
        return [{'path': name, 'rule': 'binary-review-required'}]
    findings = []
    for line_number, line in enumerate(text.splitlines(), 1):
        for rule, pattern in RULES.items():
            if pattern.search(line):
                findings.append(dict(path=name, line=line_number, rule=rule))
        for raw in IP.findall(line):
            try:
                address = ipaddress.ip_address(raw)
            except ValueError:
                continue
            if address.is_global and raw not in PUBLIC_EXAMPLES:
                findings.append(dict(path=name, line=line_number, rule='public-network-address'))
    return findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history', action='store_true', help='Also inspect reachable HEAD blobs')
    args = parser.parse_args()
    root = Path(git('rev-parse', '--show-toplevel').decode().strip())
    candidates = set(git('ls-files', '-z', '--cached', '--others', '--exclude-standard').decode().split('\0'))
    findings = []
    count = 0
    for name in sorted(candidates - {''}):
        path = root / name
        if not path.is_file():
            continue
        count += 1
        if PRIVATE_NAMES.search(name):
            findings.append(dict(path=name, rule='private-file-name'))
        findings.extend(inspect(name, path.read_bytes()))
    history = []
    if args.history:
        objects = git('rev-list', '--objects', 'HEAD').decode().splitlines()
        for entry in objects:
            oid, _, name = entry.partition(' ')
            if not name or git('cat-file', '-t', oid).strip() != b'blob':
                continue
            for finding in inspect(name, git('cat-file', 'blob', oid)):
                history.append(dict(blob=oid, **finding))
    print(json.dumps(dict(files=count, findings=findings, history_findings=history), indent=2))
    raise SystemExit(bool(findings or history))


if __name__ == '__main__':
    main()
