"""Tests for hivemind.entrance.expose.gather: reading the host's interfaces and TLS files.

Fits into the Hive:
    Mirrors src/hivemind/entrance/expose/gather.py (codingrules section 3). The TLS files are
    real files in a temporary directory, parsed by the real ``cryptography``.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.expose.gather for the module under test.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from unit.entrance.expose.support import (
    PUBLIC_HOST,
    START,
    TAILSCALE_V4,
    section,
    write_server_files,
)

from hivemind.entrance.expose import (
    MAX_TLS_FILE_BYTES,
    FakeInterfaces,
    FileState,
    InterfaceAddresses,
    gather_facts,
    host_platform,
    plan_exposure,
    read_tls_facts,
)
from hivemind.manifest.schema import EntranceExposure, EntranceSection, EntranceTlsSection
from waggle.clock import FakeClock


def _as_written(path: Path) -> Path:
    """The identity resolver: TLS paths in these tests are already absolute."""
    return path


class _UnaskedInterfaces:
    """A LocalInterfaces that fails the test if anyone asks it anything."""

    async def snapshot(self) -> tuple[InterfaceAddresses, ...]:
        """Fail: a mode that never binds a real interface must not ask for one."""
        raise AssertionError("The interfaces were read in a mode that does not need them.")


def test_read_tls_facts_describes_a_good_pair(tmp_path: Path) -> None:
    files = write_server_files(tmp_path, (PUBLIC_HOST, "*.example.test"), now=START)

    facts = read_tls_facts(
        EntranceTlsSection(cert=str(files.cert_path), key=str(files.key_path)), _as_written
    )

    assert (facts.cert, facts.key, facts.key_matches_cert) == (
        FileState.VALID,
        FileState.VALID,
        True,
    )
    assert facts.dns_names == (PUBLIC_HOST, "*.example.test")
    assert facts.not_before == START - timedelta(days=1)
    assert facts.not_after == START + timedelta(days=89)
    assert (facts.cert_path, facts.key_path) == (files.cert_path, files.key_path)


def test_read_tls_facts_resolves_relative_paths_through_the_resolver(tmp_path: Path) -> None:
    write_server_files(tmp_path, now=START)

    facts = read_tls_facts(
        EntranceTlsSection(cert="server-cert.pem", key="server-key.pem"),
        lambda path: tmp_path / path,
    )

    assert facts.cert_path == tmp_path / "server-cert.pem"
    assert facts.key_matches_cert is True


def test_read_tls_facts_reports_files_that_cannot_be_read(tmp_path: Path) -> None:
    oversized = tmp_path / "huge.pem"
    oversized.write_bytes(b"-" * (MAX_TLS_FILE_BYTES + 1))

    missing = read_tls_facts(
        EntranceTlsSection(cert=str(tmp_path / "absent.pem"), key=str(tmp_path)), _as_written
    )
    too_large = read_tls_facts(
        EntranceTlsSection(cert=str(oversized), key=str(oversized)), _as_written
    )

    assert (missing.cert, missing.key) == (FileState.UNREADABLE, FileState.UNREADABLE)
    assert (too_large.cert, too_large.key) == (FileState.UNREADABLE, FileState.UNREADABLE)
    assert missing.key_matches_cert is False


def test_read_tls_facts_reports_files_that_do_not_parse(tmp_path: Path) -> None:
    garbage = tmp_path / "garbage.pem"
    garbage.write_bytes(b"-----BEGIN CERTIFICATE-----\nnot base64\n-----END CERTIFICATE-----\n")

    facts = read_tls_facts(EntranceTlsSection(cert=str(garbage), key=str(garbage)), _as_written)

    assert (facts.cert, facts.key) == (FileState.UNPARSEABLE, FileState.UNPARSEABLE)
    assert facts.dns_names == ()
    assert facts.not_after is None


def test_read_tls_facts_treats_an_encrypted_key_as_unparseable(tmp_path: Path) -> None:
    files = write_server_files(tmp_path, now=START)
    key = serialization.load_pem_private_key(files.key_path.read_bytes(), password=None)
    locked = tmp_path / "locked.pem"
    locked.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(b"a passphrase the manifest cannot hold"),
        )
    )

    facts = read_tls_facts(
        EntranceTlsSection(cert=str(files.cert_path), key=str(locked)), _as_written
    )

    assert facts.key is FileState.UNPARSEABLE
    assert facts.key_matches_cert is False


def test_read_tls_facts_notices_a_key_from_another_certificate(tmp_path: Path) -> None:
    files = write_server_files(tmp_path, now=START)
    other = tmp_path / "other-key.pem"
    other.write_bytes(
        ec.generate_private_key(ec.SECP256R1()).private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )

    facts = read_tls_facts(
        EntranceTlsSection(cert=str(files.cert_path), key=str(other)), _as_written
    )

    assert (facts.cert, facts.key, facts.key_matches_cert) == (
        FileState.VALID,
        FileState.VALID,
        False,
    )


def test_read_tls_facts_finds_no_name_in_a_certificate_without_alt_names(tmp_path: Path) -> None:
    files = write_server_files(tmp_path, (), now=START)

    facts = read_tls_facts(
        EntranceTlsSection(cert=str(files.cert_path), key=str(files.key_path)), _as_written
    )

    assert facts.cert is FileState.VALID
    assert facts.dns_names == ()


def test_read_tls_facts_with_nothing_configured(tmp_path: Path) -> None:
    facts = read_tls_facts(EntranceTlsSection(), _as_written)

    assert (facts.cert, facts.key) == (FileState.NOT_CONFIGURED, FileState.NOT_CONFIGURED)


async def test_gather_facts_in_loopback_reads_neither_interfaces_nor_files() -> None:
    clock = FakeClock(START)

    facts = await gather_facts(EntranceSection(), _UnaskedInterfaces(), clock)

    assert facts.interfaces == ()
    assert facts.tls.cert is FileState.NOT_CONFIGURED
    assert facts.now == START
    assert facts.platform is host_platform()


async def test_gather_facts_in_tunnel_reads_the_files_but_not_the_interfaces(
    tmp_path: Path,
) -> None:
    files = write_server_files(tmp_path, now=START)
    entrance = section(
        EntranceExposure.TUNNEL,
        tls=EntranceTlsSection(cert=str(files.cert_path), key=str(files.key_path)),
    )

    facts = await gather_facts(entrance, _UnaskedInterfaces(), FakeClock(START))

    assert facts.tls.key_matches_cert is True
    assert facts.interfaces == ()


async def test_gathered_facts_let_a_vpn_host_be_planned(tmp_path: Path) -> None:
    # The adapter and the pure check together, on real files and a Linux-shaped fake host.
    files = write_server_files(tmp_path, now=START)
    interfaces = FakeInterfaces({"tailscale0": [TAILSCALE_V4], "lo": ["127.0.0.1"]})
    entrance = section(
        EntranceExposure.VPN,
        vpn_interface="tailscale0",
        tls=EntranceTlsSection(cert="server-cert.pem", key="server-key.pem"),
    )

    facts = await gather_facts(entrance, interfaces, FakeClock(START), lambda path: tmp_path / path)
    plan = plan_exposure(entrance, facts)

    assert [entry.name for entry in facts.interfaces] == ["tailscale0", "lo"]
    assert plan.remote is not None
    assert plan.remote.tls is not None
    assert plan.remote.tls.cert_path == files.cert_path
