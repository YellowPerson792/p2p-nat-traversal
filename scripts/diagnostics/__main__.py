"""Historical echo/benchmark CLI kept outside the connection proxy."""
import argparse
from overlay.identity import node_config
from overlay.config import load as load_config
from overlay.runtime import run
from .client import run_client


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('echo', 'benchmark', 'client'))
    parser.add_argument('--config', required=True)
    parser.add_argument('--node', required=True)
    parser.add_argument('--peer', required=True)
    parser.add_argument('--mode', choices=('auto', 'direct-only', 'relay-only'), default='auto')
    parser.add_argument('--delivery', choices=('reliable', 'datagram'), default='reliable')
    parser.add_argument('--duration', type=float, default=30)
    parser.add_argument('--connect-timeout', type=float, default=30)
    parser.add_argument('--rate', type=float, default=20)
    parser.add_argument('--size', type=int, default=256)
    parser.add_argument('--drain', type=float, default=2)
    parser.add_argument('--bind-host', default='0.0.0.0')
    parser.add_argument('--bind-port', type=int, default=0)
    parser.add_argument('--interface-index', type=int)
    parser.add_argument('--drop-peer-udp', action='store_true')
    parser.add_argument('--identity')
    parser.add_argument('--peer-fingerprint')
    parser.add_argument('--message')
    parser.add_argument('--output')
    args = parser.parse_args()
    if args.duration <= 0 or not 0 < args.rate <= 10000 or not 8 <= args.size <= 900:
        parser.error('Invalid duration, rate or size')
    config = node_config(load_config(args.config), args.node)
    for key in ('bind_host', 'bind_port', 'drop_peer_udp', 'delivery'):
        config[key] = getattr(args, key)
    for key in ('identity', 'peer_fingerprint', 'interface_index'):
        if getattr(args, key) is not None:
            config[key] = getattr(args, key)
    run(run_client(args, config))


if __name__ == '__main__':
    main()
