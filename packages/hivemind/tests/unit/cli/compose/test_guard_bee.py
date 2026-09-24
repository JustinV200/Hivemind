"""Tests for hivemind.cli.compose.guard's Guard Bee half: wired into every Hive the CLI composes.

Roadmap step 10.6: `build_hive` (what `hive run` runs) and `build_served_hive` (what `hive serve`
runs) both hand the Queen a Guard Bee, built from the manifest's own `[guard]` section. It files
through her own door: a relay the composition root binds to her the moment she exists, which
forwards both halves of the door unchanged and refuses a call made before it was bound.

Fits into the Hive:
    Mirrors src/hivemind/cli/compose/guard.py (codingrules section 3), split by feature from
    test_guard.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests.e2e.test_guard_bee_wiring for the wired Guard Bee's rounds and episodes on a real run.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.cli import fake_manifest
from builders.guard_bee import RecordingDoor
from builders.isolation import make_guard_report

from hivemind.cli.compose import build_hive
from hivemind.cli.compose.entrance import build_served_hive
from hivemind.cli.compose.guard import GuardDoorRelay
from hivemind.guard import GuardAction, GuardConfidence
from hivemind.manifest import load_manifest
from hivemind.workers.roles.guard_bee import GuardBee
from waggle.clock import SystemClock


async def test_the_relay_forwards_both_halves_of_the_door_once_bound() -> None:
    relay, door = GuardDoorRelay(), RecordingDoor()
    request = make_guard_report()
    critical = make_guard_report(
        recommended=GuardAction.OBSERVE, confidence=GuardConfidence.CRITICAL
    )

    relay.bind(door)
    await relay.file_guard_request(request)
    await relay.report_to_human(critical)

    assert (door.filed, door.shown) == ([request], [critical])


async def test_the_relay_refuses_a_call_made_before_it_was_bound() -> None:
    with pytest.raises(RuntimeError, match="before she was bound"):
        await GuardDoorRelay().file_guard_request(make_guard_report())


def test_hive_run_and_hive_serve_both_hand_their_queen_a_guard_bee(tmp_path: Path) -> None:
    manifest = load_manifest(fake_manifest(tmp_path), {})

    run = build_hive(manifest, environ={}, clock=SystemClock())
    served = build_served_hive(manifest, environ={}, clock=SystemClock())

    assert isinstance(run.queen_deps.guard_bee, GuardBee)
    assert isinstance(served.hive.queen_deps.guard_bee, GuardBee)
    assert run.queen_deps.guard.dire_patterns == frozenset(manifest.guard.dire_patterns)
