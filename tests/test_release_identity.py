"""Check compatibility with pre-release OpenSSL identities and signatures."""
from pathlib import Path
import pytest
from overlay.identity import initialize, public_der, openssl, sign, verify, auth_message


def test_identity_interoperates_with_openssl_both_directions(tmp_path):
    key = tmp_path / 'identity.key'
    request = initialize('release-test', key, tmp_path / 'identity.request.json')
    assert public_der(key) == openssl('pkey', '-in', key, '-pubout', '-outform', 'DER')
    challenge = {'nonce': '01' * 32, 'purpose': 'release-test'}
    message = tmp_path / 'message'
    message.write_bytes(auth_message(challenge))
    signature = openssl('pkeyutl', '-sign', '-rawin', '-inkey', key, '-in', message)
    verify(request['public_key'], challenge, signature.hex())
    pub = tmp_path / 'public.der'
    pub.write_bytes(public_der(key))
    proof = tmp_path / 'signature'
    proof.write_bytes(bytes.fromhex(sign(key, challenge)))
    openssl('pkeyutl', '-verify', '-rawin', '-pubin', '-keyform', 'DER', '-inkey', pub,
            '-in', message, '-sigfile', proof)
    legacy = tmp_path / 'legacy.key'
    legacy.write_bytes(openssl('genpkey', '-algorithm', 'ED25519'))
    verify(public_der(legacy).hex(), challenge, sign(legacy, challenge))


def test_runtime_identity_needs_no_openssl_command(tmp_path, monkeypatch):
    monkeypatch.setenv('PATH', '')
    key = tmp_path / 'identity.key'
    request = initialize('no-command', key, tmp_path / 'identity.request.json')
    signature = sign(key, {'purpose': 'test'})
    verify(request['public_key'], {'purpose': 'test'}, signature)
    with pytest.raises(ValueError):
        verify(request['public_key'], {'purpose': 'changed'}, signature)
