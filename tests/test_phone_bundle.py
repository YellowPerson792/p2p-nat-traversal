import subprocess
import sys
import zipfile

from scripts.deploy_phone import build_client


def test_client_bundle_uses_installed_crypto_runtime_and_excludes_server(tmp_path):
    app = tmp_path / "client.pyz"
    digest = build_client(app)
    assert len(digest) == 64
    with zipfile.ZipFile(app) as archive:
        names = archive.namelist()
        assert "overlay/client.py" in names
        assert "overlay/identity.py" in names
        assert "overlay/secure.py" in names
        assert "overlay/quic_tls.py" not in names
        assert "overlay/runtime.py" in names
        assert "overlay/proxy.py" in names
        assert "overlay/control.py" in names
        assert "overlay/webui.py" in names
        assert "overlay/web/client/index.html" in names
        assert "overlay/web/client/app.js" in names
        assert not any(name.startswith('overlay/web/admin/') for name in names)
        assert "overlay/device.py" in names
        assert "overlay/connection.py" in names
        assert "overlay/streams.py" in names
        assert "overlay/cli.py" in names
        assert "overlay/directory.py" not in names
        assert "overlay/admin_control.py" not in names
        assert not any(name.startswith('scripts/') for name in names)
        assert not any(name.startswith("noise/") for name in names)
        assert "overlay/server.py" not in names
        assert "websockets/asyncio/client.py" in names
        assert any("/licenses/" in name for name in names)
        assert not any(name.endswith((".pyd", ".so", ".pyc")) for name in names)
    result = subprocess.run([sys.executable, "-I", str(app), "client", "--help"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "--control-port" in result.stdout
    code = ("import sys; sys.path.insert(0, sys.argv[1]); "
            "from overlay.client import Client; import websockets, aioquic; "
            "from overlay.device import Device; from overlay.connection import Connection; "
            "from overlay.webui import resource; "
            "assert b'/ui/app.js' in resource('client', 'index.html'); "
            "assert '.pyz' in websockets.__file__; "
            "print(websockets.__version__)")
    result = subprocess.run([sys.executable, "-I", "-c", code, str(app)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "16.1.1"
    # 身份命令仍使用 OpenSSL；运行加密客户端则需要手机自己的原生密码库。
    key, request = tmp_path / "phone.key", tmp_path / "phone.request.json"
    result = subprocess.run([sys.executable, "-I", str(app), "identity", "--node", "phone",
                             "--key", str(key), "--request", str(request)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "PRIVATE KEY" in key.read_text()
    assert "PRIVATE KEY" not in request.read_text()
