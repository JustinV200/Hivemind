"""Unit tests for hivemind.exoskeleton.browser.profile: the settings that keep file dialogs shut."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hivemind.cell.fake import FakeSession
from hivemind.exoskeleton.browser.profile import LOCAL_STATE, PREFERENCES, merge, seed_profile
from hivemind.exoskeleton.errors import AttachError
from waggle.clock import FakeClock

_SCRATCH = Path("/lease/scratch")
_PROFILE = _SCRATCH / "exoskeleton" / "browser" / "profile"
_DOWNLOADS = _SCRATCH / "exoskeleton" / "browser" / "downloads"


async def _read(session: FakeSession, relative: Path | str) -> dict[str, object]:
    data = json.loads(await session.get_file(_PROFILE / relative))
    assert isinstance(data, dict)
    return data


async def test_a_fresh_profile_gets_file_dialogs_refused_and_downloads_in_scratch() -> None:
    session = FakeSession(_SCRATCH, FakeClock())

    await seed_profile(session, _PROFILE, _DOWNLOADS)

    assert (await _read(session, LOCAL_STATE)) == {"select_file_dialogs": {"allowed": False}}
    preferences = await _read(session, PREFERENCES)
    assert preferences["download"] == {
        "prompt_for_download": False,
        "default_directory": str(_DOWNLOADS),
    }
    assert preferences["savefile"] == {"default_directory": str(_DOWNLOADS)}


async def test_an_existing_profile_keeps_its_other_settings() -> None:
    session = FakeSession(_SCRATCH, FakeClock())
    existing = {"select_file_dialogs": {"allowed": True, "other": 1}, "browser": {"x": 2}}
    await session.put_file(_PROFILE / LOCAL_STATE, json.dumps(existing).encode())

    await seed_profile(session, _PROFILE, _DOWNLOADS)

    assert (await _read(session, LOCAL_STATE)) == {
        "select_file_dialogs": {"allowed": False, "other": 1},
        "browser": {"x": 2},
    }


@pytest.mark.parametrize("damaged", [b"{not json", b"[1, 2]", b"\xff\xfe"])
async def test_a_damaged_settings_file_is_replaced_by_the_settings_alone(damaged: bytes) -> None:
    session = FakeSession(_SCRATCH, FakeClock())
    await session.put_file(_PROFILE / LOCAL_STATE, damaged)

    await seed_profile(session, _PROFILE, _DOWNLOADS)

    assert (await _read(session, LOCAL_STATE)) == {"select_file_dialogs": {"allowed": False}}


async def test_a_closed_session_fails_the_attach() -> None:
    session = FakeSession(_SCRATCH, FakeClock())
    await session.close()

    with pytest.raises(AttachError, match="profile"):
        await seed_profile(session, _PROFILE, _DOWNLOADS)


def test_merge_lays_settings_over_nested_objects_without_changing_either() -> None:
    existing: dict[str, object] = {"a": {"b": 1, "c": 2}, "d": 3}
    settings: dict[str, object] = {"a": {"b": 9}, "e": {"f": 4}}

    merged = merge(existing, settings)

    assert merged == {"a": {"b": 9, "c": 2}, "d": 3, "e": {"f": 4}}
    assert existing == {"a": {"b": 1, "c": 2}, "d": 3}
