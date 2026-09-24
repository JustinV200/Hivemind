"""Test hivemind.cli.readback.isolation: `hive cells isolate|lift` over a real `hive serve`.

The Hive Stand's console isolates and lifts through the Landing Board's routes on the running
serve's loopback listener, stepping up with the operator password when the route asks: the Hive
Stand's own Cell is isolated (only the human may), `cell.isolated` lands on the Queen's trail
naming the console device as who ordered it, and a lift clears it and reports what it released. A
second lift has nothing to lift and is refused; a malformed report id is refused before the
password is even read; and nothing serving means nothing to act through.

Fits into the Hive:
    Mirrors src/hivemind/cli/readback/isolation.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests.unit.cli.entrance.test_door for `hive entrance open`, the same console shape.
"""

from __future__ import annotations

from pathlib import Path

from builders.entrance.stand import (
    PASSWORD,
    Stand,
    json_of,
    serving_stand,
    set_password,
    stand_manifest,
)
from typer.testing import Result

from hivemind.entrance.runtime import HiveEntrance
from hivemind.manifest import load_manifest
from hivemind.pheromone import TrailQuery
from hivemind.queen import Queen

_REASON = "the operator saw something"  # A short phrase for the trail, as the route asks.


async def _cells(stand: Stand, *args: str) -> Result:
    """Run `hive cells ARGS --manifest ... --password-stdin` with the operator password."""
    manifest = str(stand.manifest_path)
    return await stand.terminal.hive(
        "cells", *args, "--manifest", manifest, "--password-stdin", stdin=f"{PASSWORD}\n"
    )


def _queen(entrance: HiveEntrance) -> Queen:
    """The running Queen behind the Entrance's door."""
    queen = entrance.services.queen
    assert isinstance(queen, Queen)
    return queen


async def test_the_console_isolates_the_hive_stand_and_lifts_it_again(tmp_path: Path) -> None:
    path = stand_manifest(tmp_path)
    await set_password(path)

    async with serving_stand(path) as (stand, entrance):
        queen = _queen(entrance)
        cell_id = queen.wardens[0].cell.id
        isolated = await _cells(stand, "isolate", cell_id, "--reason", _REASON, "--json")
        [event] = await queen._deps.trail.query(TrailQuery(kind="cell.isolated"))
        stepped = await queen._deps.trail.query(TrailQuery(kind="guard.entrance_step_up"))
        lifted = await _cells(stand, "lift", cell_id)
        again = await _cells(stand, "lift", cell_id)

    assert isolated.exit_code == 0, isolated.output
    view = json_of(isolated)
    assert (view["cell_id"], view["isolated"], view["egress"]) == (cell_id, True, "untracked")
    assert event.subject_id == cell_id and event.payload["reason"] == _REASON
    assert event.payload["ordered_by"] == "human"  # The human's lever, never the Queen's.
    assert stepped  # The console stepped up with the operator password when the route asked.
    assert lifted.exit_code == 0, lifted.output
    assert f"Lifted Cell {cell_id}" in lifted.output
    assert "Tainted memory stays tainted" in lifted.output
    assert again.exit_code == 1 and "hive cells lift refused" in again.output


async def test_a_malformed_report_id_is_refused_before_anything_is_sent(tmp_path: Path) -> None:
    path = stand_manifest(tmp_path)
    await set_password(path)

    async with serving_stand(path) as (stand, entrance):
        cell_id = _queen(entrance).wardens[0].cell.id
        refused = await _cells(stand, "isolate", cell_id, "--reason", _REASON, "--report", "x")
        events = await _queen(entrance)._deps.trail.query(TrailQuery(kind="cell.isolated"))

    assert refused.exit_code == 1
    assert "hive cells isolate refused" in refused.output
    assert events == ()


async def test_with_nothing_serving_there_is_nothing_to_act_through(tmp_path: Path) -> None:
    path = stand_manifest(tmp_path)
    await set_password(path)
    stand = Stand(path, load_manifest(path, {}))

    refused = await _cells(stand, "lift", "cell_01HZZZZZZZZZZZZZZZZZZZZZZZ")

    assert refused.exit_code == 1
    assert "hive serve is not running" in refused.output
