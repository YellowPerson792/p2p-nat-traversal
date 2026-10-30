import json
from pathlib import Path

import pytest

from overlay.__main__ import main
from overlay.config import initialize, load, validate
from overlay.device import Device


def test_versioned_paths_are_portable_and_legacy_paths_keep_their_meaning(tmp_path, monkeypatch):
    folder = tmp_path / 'configuration'
    folder.mkdir()
    source = folder / 'device.json'
    source.write_text(json.dumps({'schema_version': 1, 'identity': 'identity.key', 'ca': 'server.crt'}))
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    device = Device(source)
    assert device.config['identity'] == str(folder / 'identity.key')
    assert device.config['ca'] == str(folder / 'server.crt')
    device.save()
    assert Device(source).config == device.config
    source.write_text(json.dumps({'identity': 'legacy.key', 'test_extension': True}))
    assert load(source)['identity'] == 'legacy.key'
    assert load(source)['test_extension'] is True


@pytest.mark.parametrize('config', [
    [], {'schema_version': True}, {'schema_version': 2},
    {'schema_version': 1, 'management_prt': 8765}, {'management_port': True},
    {'control_port': 0}, {'udp_port': 65536}, {'mode': 'typo'},
    {'peer_pins': {'peer': 'invalid'}}, {'server': 'https://server.example:8443'},
])
def test_invalid_client_configuration_fails_early(config):
    with pytest.raises(ValueError):
        validate(config, 'client')


def test_init_is_non_destructive_and_check_does_not_dump_private_values(tmp_path, capsys):
    source = tmp_path / 'device.json'
    initialize(source, 'client')
    before = source.read_bytes()
    with pytest.raises(FileExistsError):
        initialize(source, 'server')
    assert source.read_bytes() == before
    source.write_text(json.dumps({'schema_version': 1, 'identity': 'sensitive-local-path.key'}))
    main(['config', 'check', '--config', str(source)])
    result = capsys.readouterr().out
    assert json.loads(result)['valid'] is True
    assert 'sensitive-local-path' not in result
    with pytest.raises(SystemExit) as error:
        main(['config', 'check', '--config', str(source), '--files'])
    assert error.value.code == 2


def test_server_configuration_and_explicit_flag_precedence(tmp_path, monkeypatch):
    import uvicorn
    source = tmp_path / 'coordinator.json'
    initialize(source, 'server')
    (tmp_path / 'server.crt').write_text('test certificate placeholder')
    (tmp_path / 'server.key').write_text('test key placeholder')
    calls = []
    monkeypatch.setattr(uvicorn, 'run', lambda app, **kwargs: calls.append(kwargs))
    main(['server', '--config', str(source), '--control-port', '9443'])
    assert calls[0]['port'] == 9443
    assert calls[0]['ssl_certfile'] == str(tmp_path / 'server.crt')
    assert calls[0]['host'] == '0.0.0.0'
    assert (tmp_path / 'admin.token').is_file()


def test_ctl_derives_management_endpoint_and_credential_from_config(tmp_path, monkeypatch):
    from overlay import cli
    source = tmp_path / 'device.json'
    source.write_text(json.dumps({'schema_version': 1, 'management_port': 8877,
                                  'control_token_file': 'management.token'}))
    (tmp_path / 'management.token').write_text('local-test-credential')
    calls = []

    class Response:
        def __enter__(self):
            import io
            return io.BytesIO(b'{"state":"offline"}')

        def __exit__(self, *args):
            pass

    class Opener:
        def open(self, request, **kwargs):
            calls.append(request)
            return Response()

    monkeypatch.setattr(cli, 'build_opener', lambda *args: Opener())
    main(['ctl', '--config', str(source), 'status'])
    assert calls[0].full_url == 'http://127.0.0.1:8877/api/status'
    assert calls[0].get_header('Authorization') == 'Bearer local-test-credential'
    main(['ctl', '--config', str(source), '--url', 'http://127.0.0.1:9988', 'status'])
    assert calls[1].full_url == 'http://127.0.0.1:9988/api/status'


def test_configuration_errors_are_concise_and_do_not_echo_json_source(tmp_path, capsys):
    path = tmp_path / 'bad.json'
    path.write_text('{"sensitive-value": invalid}')
    with pytest.raises(SystemExit) as error:
        main(['config', 'check', '--config', str(path)])
    assert error.value.code == 2
    output = capsys.readouterr().err
    assert 'sensitive-value' not in output and 'Traceback' not in output


def test_checked_in_templates_match_initialized_configurations():
    root = Path(__file__).resolve().parents[1]
    for kind in ('client', 'server'):
        config = load(root / 'examples' / f'{kind}.example.json', kind, complete=kind == 'server')
        assert config['schema_version'] == 1
