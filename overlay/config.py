"""Versioned public configuration, with legacy working-directory path compatibility."""
import argparse
import ipaddress
import json
import math
from pathlib import Path


CLIENT_FIELDS = {
    'schema_version', 'server', 'control_port', 'udp_port', 'node', 'identity', 'ca',
    'management_port', 'control_token_file', 'mode', 'bind_host', 'bind_port',
    'interface_index', 'peer_pins', 'peer_fingerprint', 'delivery', 'drop_peer_udp',
}
SERVER_FIELDS = {
    'schema_version', 'host', 'control_port', 'udp_port', 'ttl', 'cert', 'key',
    'registry', 'admin_token_file',
}
PATH_FIELDS = {
    'client': ('identity', 'ca', 'control_token_file'),
    'server': ('cert', 'key', 'registry', 'admin_token_file'),
}


def port(value):
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError('Port must be an integer from 0 to 65535') from exc
    if not 0 <= number <= 65535:
        raise argparse.ArgumentTypeError('Port must be from 0 to 65535')
    return number


def validate(config, kind, *, complete=False):
    if not isinstance(config, dict):
        raise ValueError('Configuration must be a JSON object')
    if kind not in PATH_FIELDS:
        raise ValueError('Configuration kind must be client or server')
    if 'schema_version' in config:
        if type(config['schema_version']) is not int or config['schema_version'] != 1:
            raise ValueError('Unsupported schema_version; expected 1')
        allowed = CLIENT_FIELDS if kind == 'client' else SERVER_FIELDS
        unknown = sorted(set(config) - allowed)
        if unknown:
            raise ValueError('Unknown configuration fields: ' + ', '.join(unknown))
    for key in ('control_port', 'udp_port', 'management_port', 'bind_port'):
        if key in config:
            value = config[key]
            minimum = 0 if key in ('management_port', 'bind_port') else 1
            if type(value) is not int or not minimum <= value <= 65535:
                raise ValueError(f'{key} must be an integer in {minimum}..65535')
    for key in (*PATH_FIELDS[kind], 'server', 'node', 'host', 'bind_host'):
        if key in config and (not isinstance(config[key], str) or not config[key].strip()):
            raise ValueError(f'{key} must be a nonempty string')
    if 'server' in config:
        # URL normalization belongs to the setup API. Persist a host, not credentials/URLs.
        if any(c in config['server'] for c in '/:@?#') or any(c.isspace() for c in config['server']):
            raise ValueError('server must be an IPv4 address or hostname; use control_port separately')
    if 'node' in config:
        from .identity import valid_node
        if not valid_node(config['node']):
            raise ValueError('node must use ASCII letters, digits, underscores or hyphens')
    for key in ('host', 'bind_host'):
        if key in config:
            try:
                ipaddress.IPv4Address(config[key])
            except ipaddress.AddressValueError as exc:
                raise ValueError(f'{key} must be an IPv4 address') from exc
    if 'mode' in config and config['mode'] not in ('auto', 'direct-only', 'relay-only'):
        raise ValueError('mode must be auto, direct-only or relay-only')
    if 'delivery' in config and config['delivery'] not in ('reliable', 'datagram'):
        raise ValueError('delivery must be reliable or datagram')
    if 'ttl' in config and (type(config['ttl']) not in (int, float)
            or not math.isfinite(config['ttl']) or config['ttl'] <= 0):
        raise ValueError('ttl must be a finite positive number')
    if 'interface_index' in config and (type(config['interface_index']) is not int
            or not 1 <= config['interface_index'] <= 0xFFFFFF):
        raise ValueError('interface_index must be an integer in 1..16777215 (Windows only)')
    if 'drop_peer_udp' in config and type(config['drop_peer_udp']) is not bool:
        raise ValueError('drop_peer_udp must be a boolean')
    pins = config.get('peer_pins', {})
    if not isinstance(pins, dict):
        raise ValueError('peer_pins must map node names to SHA-256 fingerprints')
    for pin in [*pins.values(), *([config['peer_fingerprint']] if 'peer_fingerprint' in config else [])]:
        if not isinstance(pin, str) or len(pin) != 64 or any(c not in '0123456789abcdef' for c in pin):
            raise ValueError('Peer fingerprints must be 64 lowercase hexadecimal characters')
    if complete and kind == 'server':
        missing = [key for key in ('cert', 'key', 'registry') if not config.get(key)]
        if missing:
            raise ValueError('Server requires: ' + ', '.join(missing))
    return config


def load(path, kind='client', *, missing_ok=False, complete=False):
    path = Path(path).resolve()
    if missing_ok and not path.exists():
        return {}
    try:
        config = json.loads(path.read_text(encoding='utf-8-sig'))
    except json.JSONDecodeError as exc:
        # Do not include JSON source text, which can contain credentials.
        raise ValueError(f'Invalid JSON configuration at line {exc.lineno}, column {exc.colno}') from exc
    validate(config, kind, complete=complete)
    if config.get('schema_version') == 1:
        for key in PATH_FIELDS[kind]:
            if config.get(key):
                config[key] = str((path.parent / config[key]).resolve())
    return config


def template(kind):
    if kind == 'client':
        return dict(schema_version=1, management_port=8765, mode='auto')
    return dict(schema_version=1, host='0.0.0.0', control_port=8443, udp_port=3478,
                ttl=20, cert='server.crt', key='server.key', registry='members.json',
                admin_token_file='admin.token')


def initialize(path, kind):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Never overwrite an existing local configuration or generate credentials implicitly.
    with path.open('x', encoding='utf-8') as stream:
        path.chmod(0o600)
        json.dump(template(kind), stream, indent=2)
        stream.write('\n')
    return {'created': str(path), 'kind': kind, 'schema_version': 1}


def add_parser(commands):
    parser = commands.add_parser('config', help='Initialize or validate public JSON configuration')
    operations = parser.add_subparsers(dest='operation', required=True)
    init = operations.add_parser('init', help='Create a configuration without overwriting files')
    init.add_argument('--kind', choices=('client', 'server'), default='client')
    init.add_argument('--output', required=True)
    check = operations.add_parser('check', help='Validate without printing configuration values')
    check.add_argument('--kind', choices=('client', 'server'), default='client')
    check.add_argument('--config', required=True)
    check.add_argument('--files', action='store_true', help='Also check existing key/certificate/CA files')


def invoke(args):
    if args.operation == 'init':
        return initialize(args.output, args.kind)
    config = load(args.config, args.kind, complete=args.kind == 'server')
    if args.files:
        for key in PATH_FIELDS[args.kind]:
            if key in ('registry', 'admin_token_file', 'control_token_file'):
                continue  # Runtime creates these state files on first use.
            if config.get(key) and not Path(config[key]).is_file():
                raise ValueError(f'Configured {key} file does not exist')
    return {'valid': True, 'kind': args.kind, 'schema_version': config.get('schema_version', 'legacy'),
            'paths_relative_to': 'config' if config.get('schema_version') == 1 else 'working_directory'}
