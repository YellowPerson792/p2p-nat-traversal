"""Server TLS trust and client identity challenges over actual encrypted QUIC."""
import asyncio
from datetime import datetime, timedelta, timezone
import json

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from overlay.secure import CHALLENGE, PROOF, PeerLink, TransportIdentity
from overlay.wire import canonical
from test_identity import identities
from test_secure import pair, pump


def certificate(owner, *, before=-1, after=1, client=True, server=True):
    identity = owner.credentials
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'overlay-peer')])
    now = datetime.now(timezone.utc)
    usage = ([ExtendedKeyUsageOID.CLIENT_AUTH] if client else []) + ([ExtendedKeyUsageOID.SERVER_AUTH] if server else [])
    identity.certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(identity.private.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now + timedelta(days=before)).not_valid_after(now + timedelta(days=after))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName('overlay-peer')]), critical=False)
        .add_extension(x509.ExtendedKeyUsage(usage), critical=False)
        .sign(identity.private, algorithm=None))
    identity.pem = identity.certificate.public_bytes(serialization.Encoding.PEM)
    owner.binding = identity.bind(owner.node, owner.peer_name, owner.sid)


@pytest.mark.parametrize('attack', ['wrong_key', 'substituted_certificate', 'expired', 'future', 'wrong_purpose'])
def test_server_tls_rejects_bad_certificate_or_key(identities, attack):
    async def run():
        a, b, queue = pair(identities[1], start=False)
        delivered = []
        a.on_data = b.on_data = lambda *args: delivered.append(args)
        if attack == 'wrong_key':
            b.credentials.private = Ed25519PrivateKey.generate()
        elif attack == 'substituted_certificate':
            b.credentials = TransportIdentity(identities[1]['b']['identity'])
        else:
            options = {'expired': dict(before=-2, after=-1),
                       'future': dict(before=1, after=2),
                       'wrong_purpose': dict(client=True, server=False)}[attack]
            certificate(b, **options)
            a.peer_binding = b.binding
            with pytest.raises(ValueError):
                PeerLink(a, 'direct')
            assert not delivered and not a.ready.is_set()
            return
        a.link = PeerLink(a, 'direct')
        await pump(a, b, queue, lambda: a.link.closed)
        assert not a.link.authenticated and not delivered
    asyncio.run(run())


def test_client_identity_does_not_require_a_tls_client_certificate(identities):
    async def run():
        a, b, queue = pair(identities[1], start=False)
        a.credentials.private = None
        a.credentials.certificate = None
        a.link = PeerLink(a, 'direct')
        await pump(a, b, queue, lambda: a.ready.is_set() and b.ready.is_set())
        received = []
        b.on_data = lambda data, path: received.append(data)
        assert a.send(b'approved identity over normal TLS')
        await pump(a, b, queue, lambda: bool(received))
        assert received == [b'approved identity over normal TLS']
    asyncio.run(run())


def test_a_client_identity_proof_cannot_be_replayed_in_another_handshake(identities, monkeypatch):
    original = PeerLink.frame
    captured = []
    replay = False
    def frame(link, kind, payload=b'', **kwargs):
        if kind == PROOF:
            if replay:
                payload = captured[0]
            else:
                captured.append(payload)
        return original(link, kind, payload, **kwargs)
    monkeypatch.setattr(PeerLink, 'frame', frame)
    async def run():
        nonlocal replay
        a, b, queue = pair(identities[1])
        await pump(a, b, queue, lambda: a.ready.is_set() and b.ready.is_set())
        assert len(captured) == 1
        replay = True
        a2, b2, queue2 = pair(identities[1])
        await pump(a2, b2, queue2, lambda: b2.link is not None and b2.link.closed)
        assert not b2.link.authenticated and not a2.ready.is_set()
    asyncio.run(run())


@pytest.mark.parametrize('field', ['allocation_id', 'connection', 'certificate', 'hello'])
def test_client_rejects_a_challenge_with_the_wrong_context(identities, monkeypatch, field):
    original = PeerLink.frame
    def frame(link, kind, payload=b'', **kwargs):
        if kind == CHALLENGE:
            challenge = json.loads(payload)
            challenge[field] = '00' * 32
            payload = canonical(challenge)
        return original(link, kind, payload, **kwargs)
    monkeypatch.setattr(PeerLink, 'frame', frame)
    async def run():
        a, b, queue = pair(identities[1])
        await pump(a, b, queue, lambda: a.link.closed)
        assert not a.link.authenticated and not b.link.authenticated
    asyncio.run(run())
