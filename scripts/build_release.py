"""Build native directory distributions on each target OS (Python 3.12)."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def notices(target):
    folder = target / 'THIRD_PARTY_LICENSES'
    folder.mkdir()
    records = []
    for dist in importlib.metadata.distributions():
        name = dist.metadata['Name']
        if name.lower() in ('p2p-overlay', 'pip', 'build', 'pyproject_hooks'):
            continue
        records.append({'name': name, 'version': dist.version})
        destination = folder / name
        for file in dist.files or ():
            if '.dist-info/' in file.as_posix() and ('license' in file.as_posix().lower() or file.name == 'METADATA'):
                source = Path(dist.locate_file(file))
                if source.is_file():
                    dest = destination / Path(*file.parts[1:])
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, dest)
    license_path = Path(sys.base_prefix) / 'LICENSE.txt'
    if not license_path.is_file():
        candidates = list((Path(sys.base_prefix) / 'lib').glob('python*/LICENSE.txt'))
        if not candidates:
            raise RuntimeError('Cannot locate CPython license')
        license_path = candidates[0]
    shutil.copy2(license_path, folder / 'CPython-LICENSE.txt')
    (folder / 'DEPENDENCIES.json').write_text(json.dumps(records, indent=2) + '\n')
    shutil.copy2(ROOT / 'LICENSE', target / 'LICENSE')


def archive(folder, output, stem):
    if os.name == 'nt':
        path = output / (stem + '.zip')
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
            for source in sorted(folder.rglob('*')):
                if source.is_file():
                    z.write(source, Path(stem) / source.relative_to(folder))
    else:
        path = output / (stem + '.tar.gz')
        with tarfile.open(path, 'w:gz') as tar:
            tar.add(folder, arcname=stem)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system not in ('windows', 'linux') or machine not in ('amd64', 'x86_64'):
        parser.error('Supported native build targets: Windows x64 and Linux x64')
    with tempfile.TemporaryDirectory(prefix='overlay-release-') as tmp:
        tmp = Path(tmp)
        command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir',
                   '--name', 'p2p-overlay', '--paths', str(ROOT), '--distpath', str(tmp / 'dist'),
                   '--workpath', str(tmp / 'work'), '--specpath', str(tmp), '--collect-all', 'aioquic',
                   '--collect-all', 'cryptography', '--collect-all', 'websockets', '--collect-submodules', 'overlay',
                   '--collect-data', 'overlay', '--add-data', f'{ROOT / "overlay/web"}{os.pathsep}overlay/web']
        for module in ('fastapi', 'uvicorn', 'websockets', 'aioquic', 'cryptography'):
            command.extend(['--copy-metadata', module])
        command.append(str(ROOT / 'scripts/release/entry.py'))
        subprocess.run(command, cwd=ROOT, check=True)
        app = tmp / 'dist/p2p-overlay'
        notices(app)
        client = tmp / 'client'
        shutil.copytree(app, client)
        shutil.copy2(ROOT / 'scripts/release/CLIENT.txt', client / 'README.txt')
        shutil.copy2(ROOT / 'examples/client.example.json', client / 'client.example.json')
        if os.name == 'nt':
            (client / 'start-client.cmd').write_bytes(b'@echo off\r\n"%~dp0p2p-overlay.exe"\r\npause\r\n')
        artifacts = [archive(client, output, f'p2p-overlay-client-{version}-{system}-x64')]
        if system == 'linux':
            server = tmp / 'server'
            shutil.copytree(app, server / 'bin')
            shutil.copy2(ROOT / 'scripts/release/install-server.sh', server / 'install.sh')
            (server / 'install.sh').chmod(0o755)
            shutil.copy2(ROOT / 'scripts/release/SERVER.txt', server / 'README.txt')
            (server / 'VERSION').write_text(version + '\n')
            artifacts.append(archive(server, output, f'p2p-overlay-server-{version}-linux-x64'))
    for artifact in artifacts:
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        artifact.with_name(artifact.name + '.sha256').write_text(f'{digest}  {artifact.name}\n')
        print(f'Built {artifact.name}: {artifact.stat().st_size} bytes; SHA256 {digest}')


if __name__ == '__main__':
    main()
