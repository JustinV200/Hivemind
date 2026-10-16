"""Tests for the swarm listener's TLS, the certificate pin an invite carries and its configuration.

Fits into the Hive:
    Mirrors the TLS and configuration half of src/hivemind/swarm/gateway/listener.py (the
    connection behaviour is in test_listener.py). A device never trusts a certificate authority:
    it pins the listener's certificate by SHA-256 fingerprint, taken from its invite
    (docs/waggle/enrolment.md section 9). So these tests use throwaway self-signed certificates
    and show that exactly the pinned one is accepted.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.gateway.listener for the module under test.
    - builders.swarm.tls for the throwaway certificates.
"""

from __future__ import annotations

import ssl
from pathlib import Path

import pytest
from builders.swarm import (
    DeviceClient,
    HiveRig,
    make_device_party,
    make_hive_rig,
    make_tls_material,
)

from hivemind.manifest import SwarmSection
from hivemind.swarm.errors import SwarmError
from hivemind.swarm.gateway import ListenerConfig, LivenessRules, certificate_pin
from waggle.errors import TlsPinMismatchError
from waggle.messages.swarm import EnrolAccept


def _config(tls_cert: Path | None = None, tls_key: Path | None = None) -> ListenerConfig:
    """A loopback listener config on an OS-chosen port, with TLS when a pair is given."""
    return ListenerConfig(
        host="127.0.0.1",
        port=0,
        tls_cert=tls_cert,
        tls_key=tls_key,
        liveness=LivenessRules(miss_limit=4),
    )


def _tls_stand(tmp_path: Path) -> tuple[HiveRig, ListenerConfig, str]:
    """A stand whose invites carry the pin of a certificate the listener will serve."""
    material = make_tls_material(tmp_path, "listener")
    stand = make_hive_rig(url="wss://127.0.0.1:8720", tls_sha256=material.sha256)
    return stand, _config(material.cert_path, material.key_path), material.sha256


def test_the_certificate_pin_is_the_der_digest_of_the_files_certificate(tmp_path: Path) -> None:
    material = make_tls_material(tmp_path, "listener")

    assert certificate_pin(material.cert_path) == material.sha256


def test_the_pin_of_a_chain_is_the_pin_of_the_first_certificate(tmp_path: Path) -> None:
    leaf, issuer = make_tls_material(tmp_path, "leaf"), make_tls_material(tmp_path, "issuer")
    chain = tmp_path / "chain.pem"
    chain.write_text(leaf.cert_path.read_text() + issuer.cert_path.read_text())

    assert certificate_pin(chain) == leaf.sha256


def test_a_certificate_file_with_a_non_ascii_comment_is_pinned_like_a_bare_one(
    tmp_path: Path,
) -> None:
    # A PEM exported from PKCS#12 carries "Bag Attributes" lines naming the owner, in whatever
    # encoding the exporter used; `load_cert_chain` serves such a file, so the pin must read it.
    material = make_tls_material(tmp_path, "listener")
    annotated = tmp_path / "annotated.pem"
    annotated.write_bytes(
        "Bag Attributes\n    friendlyName: Jörg's Hive Stand\n".encode()
        + material.cert_path.read_bytes()
    )

    assert certificate_pin(annotated) == material.sha256


def test_a_certificate_block_that_is_not_valid_pem_is_a_swarm_error(tmp_path: Path) -> None:
    damaged = tmp_path / "damaged.pem"
    damaged.write_bytes(b"-----BEGIN CERTIFICATE-----\nJ\xc3\xb6rg\n-----END CERTIFICATE-----\n")

    with pytest.raises(SwarmError, match="not valid PEM"):
        certificate_pin(damaged)


def test_a_file_that_holds_no_certificate_has_no_pin(tmp_path: Path) -> None:
    empty = tmp_path / "empty.pem"
    empty.write_text("this is not a certificate\n")

    with pytest.raises(SwarmError):
        certificate_pin(empty)


def test_a_missing_certificate_file_is_a_file_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        certificate_pin(tmp_path / "absent.pem")


def test_the_config_reads_the_manifest_section_and_resolves_paths_under_the_manifest_directory(
    tmp_path: Path,
) -> None:
    section = SwarmSection(
        listen_host="10.1.2.3",
        listen_port=9001,
        advertise_url="wss://hive.example.net:9001",
        tls_cert="certs/hive.crt",
        tls_key="certs/hive.key",
        heartbeat_miss_limit=6,
    )

    config = ListenerConfig.from_section(section, tmp_path)

    assert (config.host, config.port) == ("10.1.2.3", 9001)
    assert config.tls_cert == tmp_path / "certs" / "hive.crt"
    assert config.tls_key == tmp_path / "certs" / "hive.key"
    assert config.liveness.miss_limit == 6
    assert config.invite_url == "wss://hive.example.net:9001"


def test_a_plaintext_loopback_section_has_no_certificate_and_no_pin(tmp_path: Path) -> None:
    config = ListenerConfig.from_section(SwarmSection(), tmp_path)

    assert config.tls_cert is None and config.tls_key is None
    assert config.tls_pin is None
    assert config.invite_url.startswith("ws://127.0.0.1:")


@pytest.mark.parametrize(
    ("host", "expected"),
    [("127.0.0.1", "ws://127.0.0.1:8720"), ("::1", "ws://[::1]:8720")],
)
def test_the_invite_url_is_derived_from_the_bind_address_when_none_is_advertised(
    host: str, expected: str
) -> None:
    config = ListenerConfig(host, 8720, None, None, LivenessRules(4))

    assert config.invite_url == expected


def test_a_tls_config_derives_a_wss_url_and_pins_the_served_certificate(tmp_path: Path) -> None:
    _stand, config, pin = _tls_stand(tmp_path)

    assert config.invite_url == "wss://127.0.0.1:0"
    assert config.tls_pin == pin


async def test_a_device_pinning_the_served_certificate_enrols_over_tls(tmp_path: Path) -> None:
    stand, config, pin = _tls_stand(tmp_path)
    listener = stand.listener(config)
    await listener.start()
    device = DeviceClient(stand, make_device_party(stand.clock), listener.uri, pin=pin)
    try:
        _invite, code = await stand.mint()
        await device.connect()
        await device.send_enrol(code)
        reply = await device.next()

        assert listener.uri.startswith("wss://")
        assert isinstance(reply.payload, EnrolAccept)
    finally:
        await device.close()
        await listener.stop()


async def test_a_device_pinning_another_certificate_is_refused_before_it_sends_anything(
    tmp_path: Path,
) -> None:
    stand, config, _pin = _tls_stand(tmp_path)
    impostor = make_tls_material(tmp_path, "impostor")
    listener = stand.listener(config)
    await listener.start()
    device = DeviceClient(stand, make_device_party(stand.clock), listener.uri, pin=impostor.sha256)
    try:
        with pytest.raises(TlsPinMismatchError):
            await device.connect()

        assert stand.hub.entries() == ()
        assert await stand.kinds() == []
    finally:
        await device.close()
        await listener.stop()


def test_a_certificate_file_that_does_not_exist_stops_the_listener_being_built(
    tmp_path: Path,
) -> None:
    stand = make_hive_rig(url="wss://127.0.0.1:8720", tls_sha256="0" * 64)

    with pytest.raises(FileNotFoundError):
        stand.listener(_config(tmp_path / "absent.crt", tmp_path / "absent.key"))


def test_a_key_that_is_not_the_certificates_stops_the_listener_being_built(
    tmp_path: Path,
) -> None:
    served, other = make_tls_material(tmp_path, "served"), make_tls_material(tmp_path, "other")
    stand = make_hive_rig(url="wss://127.0.0.1:8720", tls_sha256=served.sha256)

    with pytest.raises(ssl.SSLError):
        stand.listener(_config(served.cert_path, other.key_path))
