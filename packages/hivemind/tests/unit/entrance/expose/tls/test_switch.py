"""Tests for hivemind.entrance.expose.tls.switch: a revocation reaching the very next handshake.

Fits into the Hive:
    Mirrors src/hivemind/entrance/expose/tls/switch.py (codingrules section 3). The listener
    context is built once, as the remote listener would be started with it; every handshake is a
    real one through that one context, before and after ``rebuild``.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.expose.tls.switch for the module under test.
"""

from __future__ import annotations

import ssl
from datetime import datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from unit.entrance.expose.support import (
    DEVICE_ID,
    HIVE_ID,
    OTHER_DEVICE_ID,
    PUBLIC_HOST,
    ServerFiles,
    client_context,
    handshake,
    make_csr,
    now_utc,
    write_server_files,
)

from hivemind.common.secrets import MemorySecretStore
from hivemind.entrance.expose import ListenerTls
from hivemind.entrance.expose.tls import (
    ContextSwitch,
    HiveAuthority,
    RevokedSerial,
    build_crl,
    issue_client_certificate,
    load_or_create_authority,
    server_context,
)
from waggle.ids import DeviceId

_X509_V_ERR_CERT_REVOKED = 23  # OpenSSL's verify code for a revoked certificate.


class _Rig:
    """A listener context behind a switch, and two enrolled devices with certificates."""

    def __init__(self, directory: Path, authority: HiveAuthority, now: datetime) -> None:
        """Build the server, the switch and both devices."""
        self.now = now
        self.authority = authority
        self.server: ServerFiles = write_server_files(directory, now=now)
        self.tls = ListenerTls(self.server.cert_path, self.server.key_path, PUBLIC_HOST, True)
        listener = server_context(self.tls, authority, build_crl(authority, [], now))
        self.switch = ContextSwitch(listener)
        self.phone, self.phone_serial = self._device(directory / "phone", DEVICE_ID)
        self.laptop, _ = self._device(directory / "laptop", OTHER_DEVICE_ID)

    def _device(self, directory: Path, device_id: DeviceId) -> tuple[ssl.SSLContext, int]:
        """A device's client context with a fresh certificate, and its serial."""
        directory.mkdir()
        key = ec.generate_private_key(ec.SECP256R1())
        issued = issue_client_certificate(self.authority, make_csr(key), device_id, self.now)
        context = client_context(directory, self.server.certificate_pem, issued.pem, key)
        return context, issued.serial

    async def revoke_phone(self) -> None:
        """Revoke the phone's certificate the way the Entrance will: rebuild, then swap."""
        crl = build_crl(self.authority, [RevokedSerial(self.phone_serial, self.now)], self.now)
        await self.switch.rebuild(self.tls, self.authority, crl)


@pytest.fixture
async def rig(tmp_path: Path) -> _Rig:
    """A rig whose certificates are dated from the real clock, as OpenSSL judges them."""
    now = now_utc()
    authority = await load_or_create_authority(MemorySecretStore(), HIVE_ID, now)
    return _Rig(tmp_path, authority, now)


def test_the_switch_answers_every_client_hello_of_the_listener(rig: _Rig) -> None:
    assert rig.switch.listener_context.sni_callback is not None
    assert rig.switch.current is rig.switch.listener_context


async def test_a_revocation_refuses_the_next_handshake_through_the_same_listener(
    rig: _Rig,
) -> None:
    handshake(rig.phone, rig.switch.listener_context)

    await rig.revoke_phone()

    with pytest.raises(ssl.SSLCertVerificationError) as caught:
        handshake(rig.phone, rig.switch.listener_context)
    assert caught.value.verify_code == _X509_V_ERR_CERT_REVOKED
    assert handshake(rig.laptop, rig.switch.listener_context).server.version() == "TLSv1.3"
    assert rig.switch.current is not rig.switch.listener_context


async def test_a_client_hello_without_a_server_name_cannot_dodge_the_swap(rig: _Rig) -> None:
    await rig.revoke_phone()
    rig.phone.check_hostname = False

    with pytest.raises(ssl.SSLCertVerificationError):
        handshake(rig.phone, rig.switch.listener_context, server_hostname=None)


async def test_a_revoked_device_cannot_resume_a_session_from_before(rig: _Rig) -> None:
    before = handshake(rig.phone, rig.switch.listener_context)
    session = before.client.session

    await rig.revoke_phone()

    with pytest.raises(ssl.SSLCertVerificationError):
        handshake(rig.phone, rig.switch.listener_context, session=session)


async def test_an_established_connection_is_left_to_the_login_layer(rig: _Rig) -> None:
    # The swap governs handshakes only; ending live connections is the login layer's job.
    established = handshake(rig.phone, rig.switch.listener_context)

    await rig.revoke_phone()

    assert established.send(b"still here") == b"still here"


async def test_a_failed_rebuild_keeps_the_current_context(rig: _Rig) -> None:
    missing = ListenerTls(
        rig.tls.cert_path.with_name("gone.pem"), rig.tls.key_path, PUBLIC_HOST, True
    )

    with pytest.raises(OSError):
        await rig.switch.rebuild(missing, rig.authority, build_crl(rig.authority, [], rig.now))

    assert rig.switch.current is rig.switch.listener_context
    assert handshake(rig.phone, rig.switch.listener_context).server.version() == "TLSv1.3"
