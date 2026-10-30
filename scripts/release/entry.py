"""Frozen entry point; default client state survives upgrades of the program."""
import multiprocessing
import os
from pathlib import Path
import sys


def main():
    multiprocessing.freeze_support()
    from overlay.__main__ import main as run
    arguments = sys.argv[1:]
    if not arguments or arguments[0] == 'client':
        arguments = arguments or ['client']
        if not any(arg == '--config' or arg.startswith('--config=') for arg in arguments):
            if os.name == 'nt':
                base = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local'))
            else:
                base = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
            config = os.environ.get('P2P_OVERLAY_CONFIG', str(base / 'p2p-overlay/client.json'))
            arguments = [*arguments, '--config', config]
    run(arguments)


if __name__ == '__main__':
    main()
