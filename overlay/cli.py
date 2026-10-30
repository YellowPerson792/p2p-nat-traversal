"""HTTP API client: no direct registry or device-state mutation."""
import json
import os
from pathlib import Path
import ssl
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPSHandler


def add_parser(commands):
    parser = commands.add_parser('ctl', help='Use the same management HTTP API as the browser')
    parser.add_argument('--config', default=os.environ.get('P2P_OVERLAY_CONFIG', '.local/client.json'),
                        help='Client configuration for management port and credential defaults')
    parser.add_argument('--url', default=os.environ.get('P2P_OVERLAY_CONTROL_URL'))
    parser.add_argument('--token-file', default=os.environ.get('P2P_OVERLAY_TOKEN_FILE'))
    parser.add_argument('--ca', help='CA certificate for an HTTPS management endpoint')
    parser.add_argument('--admin', action='store_true', help='Use the server administrator API')
    sub = parser.add_subparsers(dest='operation', required=True)
    for name in ('status', 'peers', 'join', 'leave', 'disconnect'):
        sub.add_parser(name)
    close = sub.add_parser('close', help='Close one connection or pending request')
    close.add_argument('--id', required=True)
    setup = sub.add_parser('setup')
    setup.add_argument('--server', required=True)
    setup.add_argument('--server-ca', help='Local CA file to upload into the device configuration')
    generate = sub.add_parser('identity-generate')
    generate.add_argument('--node', required=True)
    load = sub.add_parser('identity-load')
    load.add_argument('--node', required=True)
    load.add_argument('--key', required=True)
    request = sub.add_parser('request')
    request.add_argument('--peer', required=True)
    request.add_argument('--port', type=int, required=True)
    request.add_argument('--protocol', choices=('tcp', 'udp'), default='tcp')
    request.add_argument('--local-port', type=int, default=0,
                         help='Local listener port; 0 selects a free port')
    respond = sub.add_parser('respond')
    respond.add_argument('--request', required=True)
    decision = respond.add_mutually_exclusive_group(required=True)
    decision.add_argument('--accept', action='store_true')
    decision.add_argument('--reject', action='store_true')
    mode = sub.add_parser('mode')
    mode.add_argument('value', choices=('auto', 'direct-only', 'relay-only'))
    enrollment = sub.add_parser('enrollment')
    enrollment.add_argument('--request', required=True)
    enrollment.add_argument('--fingerprint', default='')
    decision = enrollment.add_mutually_exclusive_group(required=True)
    decision.add_argument('--accept', action='store_true')
    decision.add_argument('--reject', action='store_true')
    revoke = sub.add_parser('revoke')
    revoke.add_argument('--node', required=True)


def invoke(args):
    from .config import load
    config = load(args.config, missing_ok=True) if not args.admin else {}
    args.url = args.url or f"http://127.0.0.1:{config.get('management_port', 8765)}"
    args.token_file = args.token_file or config.get('control_token_file') or (str(args.config) + '.control-token')
    url = urlsplit(args.url)
    if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.path not in ('', '/') or url.query or url.fragment:
        raise ValueError('Invalid management URL')
    if url.scheme == 'http' and url.hostname != '127.0.0.1':
        raise ValueError('Remote management requires HTTPS')
    if not args.admin and url.hostname != '127.0.0.1':
        raise ValueError('Device management is loopback-only; use an SSH forward for remote devices')
    op = args.operation
    method, path, body = 'POST', '', None
    if args.admin:
        if op == 'status':
            method, path = 'GET', 'status'
        elif op == 'enrollment':
            path = 'enrollments/' + quote(args.request, safe='')
            body = {'accept': args.accept, 'fingerprint': args.fingerprint}
        elif op == 'revoke':
            method, path = 'DELETE', 'members/' + quote(args.node, safe='')
        else:
            raise ValueError('Administrator commands: status, enrollment, revoke')
        prefix = '/admin/api/'
    else:
        prefix = '/api/'
        if op in ('status', 'peers'):
            method, path = 'GET', 'status'
        elif op in ('join', 'leave', 'disconnect'):
            path = op
        elif op == 'close':
            method, path = 'DELETE', 'connections/' + quote(args.id, safe='')
        elif op == 'setup':
            path, body = 'setup', dict(server=args.server)
            if args.server_ca:
                body['ca_pem'] = Path(args.server_ca).read_text()
        elif op == 'identity-generate':
            path, body = 'identity/generate', {'node': args.node}
        elif op == 'identity-load':
            path, body = 'identity/load', {'node': args.node, 'key_pem': Path(args.key).read_text()}
        elif op == 'request':
            path, body = 'requests', {'peer': args.peer, 'port': args.port,
                                     'protocol': args.protocol, 'local_port': args.local_port}
        elif op == 'respond':
            path, body = 'requests/' + quote(args.request, safe=''), {'accept': args.accept}
        elif op == 'mode':
            path, body = 'mode', {'mode': args.value}
        else:
            raise ValueError('This command requires --admin')
    token = Path(args.token_file).read_text().strip()
    request = Request(args.url.rstrip('/') + prefix + path, method=method,
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
        data=json.dumps(body).encode() if body is not None else None)
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context(cafile=args.ca)))
    try:
        with opener.open(request, timeout=20) as response:
            result = json.load(response)
    except HTTPError as exc:
        try:
            detail = json.load(exc).get('detail', 'Request rejected')
        except (ValueError, OSError):
            detail = 'Request rejected'
        raise ValueError(f'HTTP {exc.code}: {detail}') from exc
    return result.get('peers', []) if op == 'peers' else result
