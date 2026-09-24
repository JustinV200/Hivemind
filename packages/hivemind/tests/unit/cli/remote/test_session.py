"""Test hivemind.cli.remote.session: log in as the laptop's device, or say in one line why not.

Fits into the Hive:
    Mirrors src/hivemind/cli/remote/session.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import socket
from datetime import UTC, datetime
from pathlib import Path

import pytest
from builders.entrance.auth import PASSWORD, WRONG_PASSWORD
from builders.entrance.stand import (
    json_of,
    laptop_terminal,
    serving_stand,
    set_password,
    stand_manifest,
)
from pydantic import SecretStr

from hivemind.cli.landing import AUTHENTICATION_CODE, LandingRefusedError
from hivemind.cli.remote import (
    EnrolmentOrder,
    ProfileStore,
    RemoteProfile,
    enrol_device,
    remote_session,
)
from waggle.clock import SystemClock
from waggle.signing import Ed25519Signer


async def test_a_wrong_password_is_refused_without_naming_the_factor(tmp_path: Path) -> None:
    path = stand_manifest(tmp_path / "stand")
    await set_password(path)
    store = ProfileStore(tmp_path / "laptop")

    async with serving_stand(path) as (stand, _entrance):
        invite = json_of(await stand.entrance("invite", "--device", "laptop", "--json"))
        order = EnrolmentOrder(invite["url"], None, invite["hive_id"], "laptop", "default")
        profile = await enrol_device(store, order, SystemClock())
        await stand.entrance("approve", profile.device_id, "--spend-cap", "5", "--yes")
        with pytest.raises(LandingRefusedError) as refused:
            wrong = SecretStr(WRONG_PASSWORD)
            async with remote_session(store, "default", wrong, SystemClock()):
                pass  # Never reached: the login itself is refused.

    assert refused.value.status == 401
    assert refused.value.body.error == AUTHENTICATION_CODE
    assert WRONG_PASSWORD not in str(refused.value)


async def test_an_entrance_that_does_not_answer_is_one_line_and_exit_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "laptop"
    laptop = laptop_terminal(home)
    for name, value in (laptop.env or {}).items():
        monkeypatch.setenv(name, value)
    # The laptop's own store, as ``hive`` finds it under the config directory just set.
    store = ProfileStore.for_user()
    await store.save(_profile_at(_closed_port()), Ed25519Signer.generate())

    result = await laptop.hive("inbox", "--remote", "--password-stdin", stdin=f"{PASSWORD}\n")

    assert result.exit_code == 1
    assert result.output.startswith("hive inbox --remote refused: The Entrance at http://127.0.0")
    assert "did not answer" in result.output and PASSWORD not in result.output
    assert store.root.is_relative_to(home)  # The test wrote where the laptop reads.


def _closed_port() -> int:
    """A loopback port nothing listens on: bound once, then released."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _profile_at(port: int) -> RemoteProfile:
    """A profile whose Entrance is ``127.0.0.1:port``."""
    return RemoteProfile(
        name="default",
        entrance_url=f"http://127.0.0.1:{port}",
        ca_file=None,
        hive_id="hive_01J8Z3Q4X5Y6Z7A8B9C0D1E2F3",
        device_id="device_01J8Z3Q4X5Y6Z7A8B9C0D1E2F3",
        device_name="laptop",
        fingerprint="MFRG-GZDF-MZTW-Q2LK",
        hive_public_key_hex="ab" * 32,
        enrolled_at=datetime(2026, 9, 24, 12, 0, tzinfo=UTC),
    )
