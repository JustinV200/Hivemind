"""Test hivemind.cli.remote.profiles: a profile is plain JSON, its key lives only beside it.

Fits into the Hive:
    Mirrors src/hivemind/cli/remote/profiles.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import stat
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hivemind.cli.landing import LandingError
from hivemind.cli.remote import ProfileStore, RemoteProfile, check_profile_name
from waggle.signing import Ed25519Signer

_AT = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def _profile(name: str = "default") -> RemoteProfile:
    """A profile as enrolment keeps it."""
    return RemoteProfile(
        name=name,
        entrance_url="https://hive.example.ts.net:8711",
        ca_file=None,
        hive_id="hive_01J8Z3Q4X5Y6Z7A8B9C0D1E2F3",
        device_id="device_01J8Z3Q4X5Y6Z7A8B9C0D1E2F3",
        device_name="laptop",
        fingerprint="MFRG-GZDF-MZTW-Q2LK",
        hive_public_key_hex="ab" * 32,
        enrolled_at=_AT,
    )


async def test_a_saved_profile_reads_back_with_its_key(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path)
    signer = Ed25519Signer.generate()

    await store.save(_profile(), signer)

    assert store.load("default") == _profile()
    assert (await store.signer(_profile())).public_key_bytes == signer.public_key_bytes
    assert store.names() == ("default",)


async def test_the_private_key_is_never_in_the_profile_file(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path)
    signer = Ed25519Signer.generate()

    await store.save(_profile(), signer)

    text = (tmp_path / "profiles" / "default.json").read_text(encoding="utf-8")
    raw = signer.private_key_bytes
    assert raw.hex() not in text and "private" not in text.lower()
    assert (tmp_path / "keys" / "default.ed25519").read_bytes() == raw


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
async def test_the_key_and_the_profile_are_owner_only(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path)

    await store.save(_profile(), Ed25519Signer.generate())

    for path in (tmp_path / "keys" / "default.ed25519", tmp_path / "profiles" / "default.json"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


async def test_forgetting_a_profile_removes_its_key_too(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path)
    await store.save(_profile(), Ed25519Signer.generate())

    existed = await store.forget("default")
    again = await store.forget("default")

    assert existed and not again
    assert store.names() == ()
    assert not (tmp_path / "keys" / "default.ed25519").exists()


def test_a_missing_profile_says_to_enrol(tmp_path: Path) -> None:
    with pytest.raises(LandingError, match="hive remote enrol"):
        ProfileStore(tmp_path).load("garden")


def test_a_damaged_profile_is_named(tmp_path: Path) -> None:
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles" / "default.json").write_text("{", encoding="utf-8")

    with pytest.raises(LandingError, match="damaged"):
        ProfileStore(tmp_path).load("default")


async def test_a_missing_key_is_named_not_shown(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path)
    await store.save(_profile(), Ed25519Signer.generate())
    (tmp_path / "keys" / "default.ed25519").unlink()

    with pytest.raises(LandingError, match="missing or damaged"):
        await store.signer(_profile())


@pytest.mark.parametrize("name", ["Default", "../escape", "", "a" * 33, "with space"])
def test_a_profile_name_must_be_short_lower_case_and_file_safe(name: str) -> None:
    with pytest.raises(LandingError):
        check_profile_name(name)


def test_a_profile_knows_its_entrance_and_pinned_ca(tmp_path: Path) -> None:
    ca_file = tmp_path / "ca.pem"
    profile = _profile().model_copy(update={"ca_file": str(ca_file)})

    address = profile.address()

    assert address.origin == "https://hive.example.ts.net:8711"
    assert address.ca_file == ca_file
