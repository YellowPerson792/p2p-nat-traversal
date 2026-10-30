"""Create/reuse a project venv and install locked runtime/test dependencies.

Uses the invoking Python; never recreates an existing environment or touches
credentials. Run from any directory with Python 3.12+.
"""
import argparse
from pathlib import Path
import subprocess
import sys
import os
import venv

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--venv', type=Path, default=ROOT / ('.venv-win' if os.name == 'nt' else '.venv'))
    parser.add_argument('--runtime-only', action='store_true', help='Omit pytest and its dependencies')
    parser.add_argument('--unlocked', action='store_true', help='Resolve project constraints instead of pinned locks')
    args = parser.parse_args()
    if sys.version_info < (3, 12):
        parser.error('Run this script with Python 3.12 or newer')
    folder = args.venv.resolve()
    python = folder / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.exists():
        if folder.exists() and any(folder.iterdir()):
            parser.error('Refusing to create a venv over a nonempty directory')
        venv.EnvBuilder(with_pip=True).create(folder)
    # Editable source install requires setuptools even in Python 3.12 venvs.
    # pip's isolated PEP 517 build installs the declared backend automatically.
    def pip(*arguments):
        subprocess.run([str(python), '-m', 'pip', *arguments], cwd=ROOT, check=True)
    if args.unlocked:
        pip('install', '-e', '.' if args.runtime_only else '.[test]')
    else:
        pip('install', '-r', 'requirements.lock' if args.runtime_only else 'requirements-dev.lock')
        pip('install', '--no-deps', '-e', '.')
    subprocess.run([str(python), '-m', 'overlay', 'doctor'], cwd=ROOT, check=True)
    print(f'Ready: {python}')
    if not args.runtime_only:
        print(f'Test: "{python}" -m pytest -q')


if __name__ == '__main__':
    main()
