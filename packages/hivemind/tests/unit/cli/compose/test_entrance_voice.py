"""Test `hive serve` voice wiring: the Entrance hears through the Hive's own registry and scanner.

Roadmap 10.5f: with `[entrance.voice]` on, the Entrance's transcriber is the TRANSCRIBER slot bound
through the Hive's own registry and Fanner (a metered transcriber) and its scanner is the Queen's;
with voice off nothing is wired; a transcriber whose provider cannot transcribe refuses to start.
Each test composes the Hive `hive serve` runs (the fake provider, the Hive's own SQLite file) and
enters ``serve_hive`` on loopback.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from builders.cli import fake_manifest

from hivemind.cli.compose.entrance import ServedHive, build_served_hive, serve_hive
from hivemind.entrance.voice import VoiceServices
from hivemind.forage.slots import ModelSlot
from hivemind.llm import FannerTranscriptionGate, TranscriptionUnsupportedError
from hivemind.manifest import load_manifest
from waggle.clock import SystemClock

_LOOPBACK = 'bind = "127.0.0.1:0"\n'  # Voice needs no remote listener to be wired.


def _served(tmp_path: Path, entrance: str, transcriber: str = "fake") -> ServedHive:
    """Compose `hive serve`'s Hive over a fake manifest with ``entrance`` as its section."""
    path = fake_manifest(tmp_path)
    text = path.read_text(encoding="utf-8")
    if transcriber != "fake":
        # The fake manifest binds every slot to "fake"; this binds the transcriber elsewhere.
        text = text.replace(
            '[llm.slots.transcriber]\nprovider = "fake"',
            f'[llm.providers.{transcriber}]\nkind = "{transcriber}"\n\n'
            f'[llm.slots.transcriber]\nprovider = "{transcriber}"',
        )
    path.write_text(text + f"\n[entrance]\n{entrance}", encoding="utf-8")
    return build_served_hive(load_manifest(path, {}), environ={}, clock=SystemClock())


async def _voice(served: ServedHive) -> VoiceServices | None:
    """Enter serve_hive and return the voice the Entrance was built with."""
    async with serve_hive(served) as entrance:
        return entrance.services.voice


def test_voice_hears_through_the_hives_own_registry_fanner_and_scanner(tmp_path: Path) -> None:
    served = _served(tmp_path, _LOOPBACK)

    voice = asyncio.run(_voice(served))

    # Metered by the Hive's own Fanner, so every clip is one llm.call on its trail.
    assert voice is not None and isinstance(voice.ears.gate, FannerTranscriptionGate)
    assert voice.ears.bound.slot is ModelSlot.TRANSCRIBER
    assert voice.scanner is served.hive.queen_deps.scanner
    assert (voice.rules.confirm_goals, voice.rules.keep_audio) == (True, False)


def test_voice_off_is_never_wired(tmp_path: Path) -> None:
    served = _served(tmp_path, _LOOPBACK + "\n[entrance.voice]\nenabled = false\n")

    assert asyncio.run(_voice(served)) is None


def test_a_transcriber_that_cannot_transcribe_refuses_to_start(tmp_path: Path) -> None:
    served = _served(tmp_path, _LOOPBACK, transcriber="anthropic")

    with pytest.raises(TranscriptionUnsupportedError, match="cannot transcribe"):
        asyncio.run(_voice(served))
