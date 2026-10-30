# 命令入口只组装配置；网络收发在 client.py / server.py，报文格式在 wire.py。
import argparse
import asyncio
import json
import os
import signal


def build_parser():
    parser = argparse.ArgumentParser(prog="p2p-overlay")
    from . import __version__
    from .config import port
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    commands = parser.add_subparsers(dest="command", required=True)
    server = commands.add_parser("server")
    server.add_argument('--config', default=os.environ.get('P2P_OVERLAY_SERVER_CONFIG'),
                        help='Server JSON configuration; explicit flags override it')
    server.add_argument("--host")
    # 同一个服务器有两个独立监听端口：TCP/TLS 8443 用于协调，UDP 3478
    # 用于地址注册和中继。端口号 3478 并不意味着实现了标准 STUN/TURN。
    server.add_argument("--control-port", type=port)
    server.add_argument("--udp-port", type=port)
    server.add_argument("--ttl", type=float)
    server.add_argument("--cert")
    server.add_argument("--key")
    server.add_argument("--registry", help="Administrator-maintained identity approval registry")
    server.add_argument("--admin-token-file", help="Server administrator credential file")
    identity = commands.add_parser("identity", help="Generate a local key and public approval request")
    identity.add_argument("--node", required=True)
    identity.add_argument("--key", required=True, help="New private key path; never send this file")
    identity.add_argument("--request", required=True, help="Public request to give to the administrator")
    sub = commands.add_parser("client", help="Run the device browser/API; configure identity and server in the UI")
    sub.add_argument("--config", default=os.environ.get('P2P_OVERLAY_CONFIG', '.local/client.json'))
    sub.add_argument("--control-port", type=port, help='Local management port (overrides management_port)')
    sub.add_argument("--control-token-file", help="CLI management credential file; default CONFIG.control-token")
    from .cli import add_parser
    add_parser(commands)
    from .config import add_parser as add_config_parser
    add_config_parser(commands)
    commands.add_parser('doctor', help='Check local runtime dependencies without opening network connections')
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        dispatch(args, parser)
    except (ValueError, OSError) as exc:
        parser.exit(2, str(exc) + '\n')


def dispatch(args, parser):
    if args.command == 'doctor':
        from .doctor import diagnose
        result = diagnose()
        print(json.dumps(result, indent=2))
        if not result['ok']:
            parser.exit(1)
        return
    if args.command == 'config':
        from .config import invoke
        print(json.dumps(invoke(args), indent=2))
        return
    if args.command == "ctl":
        from .cli import invoke
        try:
            print(json.dumps(invoke(args), indent=2))
        except (ValueError, OSError) as exc:
            parser.exit(2, str(exc) + "\n")
        return
    if args.command == "identity":
        from .identity import initialize
        print(json.dumps(initialize(args.node, args.key, args.request), indent=2))
        return
    if args.command == "server":
        from pathlib import Path
        from .config import load, validate
        settings = load(args.config, 'server') if args.config else {}
        defaults = dict(host='0.0.0.0', control_port=8443, udp_port=3478, ttl=20)
        for key in ('host', 'control_port', 'udp_port', 'ttl', 'cert', 'key', 'registry', 'admin_token_file'):
            value = getattr(args, key)
            if value is not None:
                settings[key] = value
            elif key not in settings and key in defaults:
                settings[key] = defaults[key]
        validate(settings, 'server', complete=True)
        for key in ('cert', 'key'):
            if not Path(settings[key]).is_file():
                raise ValueError(f'Server {key} file does not exist')
        for key, value in settings.items():
            setattr(args, key, value)
        import uvicorn
        from .server import create_app
        # 缺失/空审批表拒绝所有客户端；没有旧共享 token 回退路径。
        from .management import credential_file
        token_file = args.admin_token_file or (args.registry + '.admin-token')
        admin_token = credential_file(token_file)
        print(f'Administrator page: /admin ; credential file: {token_file}', flush=True)
        uvicorn.run(create_app(args.registry, args.host, args.udp_port, args.ttl, admin_token), host=args.host,
                    port=args.control_port, ssl_certfile=args.cert, ssl_keyfile=args.key,
                    log_level="warning", ws_max_size=16384)
    else:
        from .device import run_device
        if args.control_port is not None and not 0 <= args.control_port <= 65535:
            parser.error("control-port must be in 0..65535")
        async def run():
            loop = asyncio.get_running_loop()
            task = asyncio.current_task()
            if os.name != "nt":
                loop.add_signal_handler(signal.SIGTERM, task.cancel)
            try:
                await run_device(args)
            except asyncio.CancelledError:
                pass

        try:
            from .runtime import run as run_async
            run_async(run())
        except KeyboardInterrupt:
            pass
        except asyncio.TimeoutError:
            raise SystemExit(2)


if __name__ == "__main__":
    main()
