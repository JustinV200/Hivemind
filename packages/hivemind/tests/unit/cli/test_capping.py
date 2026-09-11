"""Tests for hivemind.cli.capping: `hive capping queue|show`.

Fits into the Hive:
    Mirrors src/hivemind/cli/capping.py (codingrules section 3: tests/unit mirrors src/
    one-to-one). Drives the typer application through typer.testing.CliRunner, with a
    `MemoryPheromoneTrail` seeded directly through `PheromoneTrail.record` (the same v0 shape
    `hivemind.supervision.capping.gate.CappingGate` itself writes) and handed to the CLI by
    monkeypatching `hivemind.cli.capping.open_trail`, the same seam `test_trail.py`'s own
    `--follow` test uses.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.capping for the module under test.
    - hivemind.supervision.capping.gate for the real writer of every capping.* event kind used
      here.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import JsonValue
from typer.testing import CliRunner

import hivemind.cli.capping as capping_module
from hivemind.cli.app import app
from hivemind.cli.stores import open_trail
from hivemind.pheromone import CappingEvent, MemoryPheromoneTrail
from waggle.clock import Clock, FakeClock
from waggle.ids import (
    HiveId,
    MessageId,
    NodeId,
    new_event_id,
    new_hive_id,
    new_message_id,
    new_node_id,
    new_task_id,
)

runner = CliRunner()


def _event(
    clock: Clock,
    kind: str,
    subject_id: MessageId,
    hive_id: HiveId,
    node_id: NodeId,
    **payload: JsonValue,
) -> CappingEvent:
    """Build one well-formed CappingEvent, the same shape CappingGate itself records."""
    return CappingEvent(
        id=new_event_id(clock),
        hive_id=hive_id,
        node_id=node_id,
        at=clock.now(),
        actor="system",
        kind=kind,
        subject_id=subject_id,
        payload=payload,
    )


async def _record_applied_proposal(
    trail: MemoryPheromoneTrail, clock: FakeClock, hive_id: HiveId, node_id: NodeId
) -> MessageId:
    """Record PROPOSED -> CHECKING -> CAPPED -> APPLIED: non-terminal, stays in the queue."""
    proposal = new_message_id(clock)
    task = new_task_id(clock)
    await trail.record(
        _event(
            clock,
            "capping.proposed",
            proposal,
            hive_id,
            node_id,
            task_id=task,
            tier="SCRATCH_WRITE",
        )
    )
    clock.advance(1)
    await trail.record(
        _event(
            clock, "capping.checked", proposal, hive_id, node_id, check="SCHEMA", outcome="PASSED"
        )
    )
    clock.advance(1)
    await trail.record(
        _event(
            clock,
            "capping.capped",
            proposal,
            hive_id,
            node_id,
            tier="SCRATCH_WRITE",
            checks_passed=1,
        )
    )
    clock.advance(1)
    await trail.record(
        _event(clock, "capping.applied", proposal, hive_id, node_id, action_kind="DIFF")
    )
    return proposal


async def _record_verified_proposal(
    trail: MemoryPheromoneTrail, clock: FakeClock, hive_id: HiveId, node_id: NodeId
) -> MessageId:
    """Record PROPOSED -> VERIFIED: terminal, must never appear in the queue."""
    proposal = new_message_id(clock)
    task = new_task_id(clock)
    await trail.record(
        _event(
            clock,
            "capping.proposed",
            proposal,
            hive_id,
            node_id,
            task_id=task,
            tier="OUTSIDE_SCRATCH_WRITE",
        )
    )
    clock.advance(1)
    await trail.record(
        _event(clock, "capping.verified", proposal, hive_id, node_id, postconditions_held=1)
    )
    return proposal


async def _record_just_proposed(
    trail: MemoryPheromoneTrail, clock: FakeClock, hive_id: HiveId, node_id: NodeId
) -> MessageId:
    """Record PROPOSED only: non-terminal, the most recent event of _seed_trail's three."""
    proposal = new_message_id(clock)
    task = new_task_id(clock)
    await trail.record(
        _event(clock, "capping.proposed", proposal, hive_id, node_id, task_id=task, tier="COMMAND")
    )
    return proposal


async def _seed_trail() -> tuple[MemoryPheromoneTrail, str, str, str]:
    """Populate a MemoryPheromoneTrail with three proposals, spaced out on a FakeClock.

    One ends APPLIED (non-terminal), one VERIFIED (terminal), one just PROPOSED (non-terminal,
    the most recent), so their latest-event order is unambiguous.

    Returns:
        The trail, and the three proposals' ids (applied, verified, just-proposed) in that order.
    """
    clock = FakeClock()
    hive_id, node_id = new_hive_id(clock), new_node_id(clock)
    trail = MemoryPheromoneTrail(clock)

    proposal_a = await _record_applied_proposal(trail, clock, hive_id, node_id)
    clock.advance(10)
    proposal_b = await _record_verified_proposal(trail, clock, hive_id, node_id)
    clock.advance(10)
    proposal_c = await _record_just_proposed(trail, clock, hive_id, node_id)

    return trail, str(proposal_a), str(proposal_b), str(proposal_c)


def _seeded(monkeypatch: pytest.MonkeyPatch) -> tuple[str, str, str]:
    """Seed a trail, monkeypatch it into hivemind.cli.capping.open_trail, return the three ids."""
    trail, proposal_a, proposal_b, proposal_c = asyncio.run(_seed_trail())
    monkeypatch.setattr(capping_module, "open_trail", lambda path: trail)
    return proposal_a, proposal_b, proposal_c


# ──────────────────────────────────────────────────────────────────────────────
# hive capping queue
# ──────────────────────────────────────────────────────────────────────────────


def test_queue_excludes_terminal_proposals_and_orders_newest_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proposal_a, proposal_b, proposal_c = _seeded(monkeypatch)

    result = runner.invoke(app, ["capping", "queue", "--db", str(tmp_path / "hive.sqlite3")])

    assert result.exit_code == 0
    lines = result.output.strip().splitlines()
    assert lines[0].startswith("PROPOSAL")
    body = lines[1:]
    assert len(body) == 2  # proposal_b (VERIFIED) is terminal and excluded.
    assert proposal_b not in result.output
    # Newest latest-event first: proposal_c (just proposed, latest) before proposal_a (applied).
    assert body[0].startswith(proposal_c)
    assert body[1].startswith(proposal_a)


def test_queue_json_carries_task_id_tier_and_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proposal_a, _, _ = _seeded(monkeypatch)

    result = runner.invoke(
        app, ["capping", "queue", "--db", str(tmp_path / "hive.sqlite3"), "--json"]
    )

    assert result.exit_code == 0
    rows = {row["proposal_id"]: row for row in json.loads(result.output)}
    assert rows[proposal_a]["state"] == "APPLIED"
    assert rows[proposal_a]["tier"] == "SCRATCH_WRITE"
    assert rows[proposal_a]["age_s"] > 0


# ──────────────────────────────────────────────────────────────────────────────
# hive capping show
# ──────────────────────────────────────────────────────────────────────────────


def test_show_prints_one_proposals_events_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proposal_a, _, _ = _seeded(monkeypatch)

    result = runner.invoke(
        app, ["capping", "show", proposal_a, "--db", str(tmp_path / "hive.sqlite3")]
    )

    assert result.exit_code == 0
    lines = result.output.strip().splitlines()
    kinds = [line.split("  ")[1] for line in lines]
    assert kinds == ["capping.proposed", "capping.checked", "capping.capped", "capping.applied"]


def test_show_json_carries_each_events_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proposal_a, _, _ = _seeded(monkeypatch)

    result = runner.invoke(
        app,
        ["capping", "show", proposal_a, "--db", str(tmp_path / "hive.sqlite3"), "--json"],
    )

    assert result.exit_code == 0
    events = json.loads(result.output)
    assert [e["kind"] for e in events] == [
        "capping.proposed",
        "capping.checked",
        "capping.capped",
        "capping.applied",
    ]
    assert events[2]["payload"] == {"tier": "SCRATCH_WRITE", "checks_passed": 1}


def test_show_unknown_proposal_prints_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seeded(monkeypatch)

    result = runner.invoke(
        app, ["capping", "show", "msg_unknown", "--db", str(tmp_path / "hive.sqlite3")]
    )

    assert result.exit_code == 0
    assert result.output.strip() == ""


def test_queue_reads_a_real_sqlite_trail(tmp_path: Path) -> None:
    # Deliberately does not monkeypatch open_trail, unlike every other test above: replacing it
    # with an in-memory trail is exactly what hid a nested `asyncio.run` inside _capping_events
    # until `hive capping queue` was run against a real file for the first time.
    db = tmp_path / "hive.sqlite3"
    open_trail(db)  # Applies the migrations, so the command opens a real, current schema.

    result = runner.invoke(app, ["capping", "queue", "--db", str(db)])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[0].startswith("PROPOSAL")


def test_show_reads_a_real_sqlite_trail(tmp_path: Path) -> None:
    # The same guard for `show`, which opens the trail on its own line rather than inline.
    db = tmp_path / "hive.sqlite3"
    open_trail(db)

    result = runner.invoke(app, ["capping", "show", "msg_unknown", "--db", str(db)])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == ""
