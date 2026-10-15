"""End-to-end tests for watch mode and the Patrol: roadmap step 11.10, on real Cells.

A whole Hive is built over a `builders.cli.fake_manifest` whose `[supervision.watch]` table is
compressed to a fifth of a second between samples and a second between Patrols, then run with
`hivemind.cli.compose.run_hive` on a real clock and a real SQLite file. Nothing about the Cell is
faked. The Hive Stand's Warden looks at this machine through its real `LocalProcessSession` with
the real fixed commands (`sh`, `find` and `tail` reading `/proc`); a device's Warden asks a real
Pollen Packet, running in this process and enrolled through the real ceremony. Each keeps what it
saw as C2 Bee Bread and reviews it with a scripted model that decides only from what its prompt
actually shows it. The scenarios:

- **a planted CPU hog becomes an Alarm the human hears of.** A real child process (a copy of the
  shell named `beehog_zq7`, spinning) starts while the Warden watches. The pre-filter flags it as
  a new process over the CPU share; the scripted model, which sees the process by name in the
  fenced facts, decides ALARM; a `PATROL_ANOMALY` reaches the Queen, who escalates it to the human
  (`ChatKind.ALARM`). The hog's name is in the stored C2 observation and nowhere on the trail or in
  the chat, and the watched directory and the Warden's scratch are exactly as found.
- **a quiet interval records nothing notable.** The same Hive with nothing planted: the Patrol's
  outcome is NOTHING, no Alarm is raised, and the observations it covered are gone.
- **watching pauses while a real bee works and resumes after.** The three-haiku goal runs with
  watching on; no observation is kept while the Warden is ACTIVE, and sampling carries on once the
  Drone's task is done.
- **a device is watched through its packet, and each Warden keeps only its own samples.** The Hive
  Stand's Warden and an enrolled laptop's Warden watch at once into the one memory store, the hog
  is seen by both, both Patrols alarm, and every kept observation names the Warden that kept it
  and describes that Warden's own machine. Watching takes no lease and sends no command.

Where the Hive Stand's Warden is the one watching, `LocalProcessSession`'s write methods are
replaced with ones that fail the test: a Warden that is only watching has no business calling them.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/roadmap.md step 11.10 for the step these scenarios exercise.
    - docs/adr/0054-access-levels-watch-mode-and-the-patrol.md for the design.
    - tests/e2e/watch_helpers.py for the Hive, the scripted model and the planted hog.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from functools import partial
from pathlib import Path

import pytest
from e2e.kernel_helpers import HaikuScript, default_worker_turn, snapshot_tree, wait_until
from e2e.scripted_openai import free_port
from e2e.swarm.device import enrol, running
from e2e.swarm.hive import enrolled_node, trail_events
from e2e.watch_helpers import (
    HOG_NAME,
    WAIT_S,
    PatrolModel,
    as_found,
    events_of,
    everything_said,
    forbid_session_writes,
    kept_by,
    patrolled,
    planted_hog,
    stored_entries,
    swarm_watching_hive,
    watched_directory,
    watching_hive,
)

from hivemind.cell import AccessLevel, CombShieldLevel, HoneyClearance, hive_stand_cell_id
from hivemind.cli.compose import Hive, run_goal, run_hive
from hivemind.cli.compose.deps.watch import device_id_of_cell
from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.queen.chat import ChatKind, ChatQuery
from hivemind.swarm import SwarmNode
from hivemind.wardens import WardenState
from waggle.ids import LeaseId

pytestmark = pytest.mark.e2e

_HAIKU_GOAL = "write three haiku about bees to separate files"
_HOG_SHARE = 0.25 / (os.cpu_count() or 1)  # What the Hives of these tests call a hog's least.


def _stand_of(hive: Hive) -> str:
    """The id of the Hive Stand's own Warden, which owns what that Warden kept."""
    return str(hive.warden_link.warden_id)


# ──────────────────────────────────────────────────────────────────────────────
# A planted CPU hog becomes an Alarm
# ──────────────────────────────────────────────────────────────────────────────


def test_a_planted_cpu_hog_becomes_a_patrol_anomaly_alarm_the_human_hears_of(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    watched = watched_directory(tmp_path)
    hive = watching_hive(tmp_path, PatrolModel(), watched)
    writes = forbid_session_writes(monkeypatch)

    asyncio.run(_plant_a_hog_and_wait_for_the_alarm(tmp_path, hive, watched))

    assert writes == []


async def _plant_a_hog_and_wait_for_the_alarm(tmp_path: Path, hive: Hive, watched: Path) -> None:
    """The async body of the hog scenario: watch, plant, wait for the Alarm, then check it all."""
    stand = _stand_of(hive)
    async with run_hive(hive):
        lease = hive.warden.lease
        assert lease is not None
        watched_before, scratch_before = as_found(watched), snapshot_tree(lease.scratch_root)
        await wait_until(lambda: _has_kept(hive, stand), timeout_s=WAIT_S)

        with planted_hog(tmp_path):
            await wait_until(lambda: patrolled(hive, "ALARM"), timeout_s=WAIT_S)
            await wait_until(lambda: _human_was_alarmed(hive), timeout_s=WAIT_S)

        events = list(await hive.stores.trail.query(TrailQuery()))
        chat = await hive.stores.chat.read(ChatQuery())
        patrol_alarms = await _escalated_patrol_alarms(hive)
        kept = await kept_by(hive, stand)
        assert as_found(watched) == watched_before  # Nothing was written, renamed or touched.
        assert snapshot_tree(lease.scratch_root) == scratch_before

    # The real process was seen by name, by the real commands, and kept as C2 Bee Bread.
    seen = [p for item in kept for p in item.observation.processes if p.name == HOG_NAME]
    assert seen and max(p.cpu_share for p in seen) >= _HOG_SHARE
    lines = {line.line for item in kept for line in item.observation.log_lines}
    assert "started" in lines  # The log under the operator's root was read too.
    await _assert_every_entry_is_c2(hive)

    # The Alarm is a PATROL_ANOMALY the Queen escalated; none of what the machine said is on the
    # trail or in the chat, only that a Patrol raised it.
    raised = [e for e in events if e.kind == "alarm.raised"]
    assert [e.payload["kind"] for e in raised].count("PATROL_ANOMALY") >= 1
    assert patrol_alarms <= {e.subject_id for e in raised}  # Escalated is what a Patrol raised.
    assert any(line.kind is ChatKind.ALARM and line.ref in patrol_alarms for line in chat)
    assert HOG_NAME not in everything_said(events)
    assert HOG_NAME not in json.dumps([line.model_dump(mode="json") for line in chat])
    patrols = [e for e in events if e.kind == "warden.patrol"]
    assert all(set(e.payload) == {"outcome", "observations", "signals"} for e in patrols)


async def _has_kept(hive: Hive, *wardens: str) -> bool:
    """Whether every one of `wardens` has an Observation in the store right now."""
    return all([await kept_by(hive, warden) for warden in wardens])


async def _human_was_alarmed(hive: Hive) -> bool:
    """Whether the Queen has escalated a Patrol's Alarm to the human's chat."""
    lines = await hive.stores.chat.read(ChatQuery())
    patrol_alarms = await _escalated_patrol_alarms(hive)
    return any(line.kind is ChatKind.ALARM and line.ref in patrol_alarms for line in lines)


async def _escalated_patrol_alarms(hive: Hive) -> set[str]:
    """The ids of every PATROL_ANOMALY Alarm the Queen escalated, from her `alarm.escalated` rows.

    Any Alarm in the chat is not enough: on a loaded machine the Queen's liveness sweep can put a
    `CELL_UNREACHABLE` there first (the test manifest's heartbeat is 50 ms), and that one is not
    the Patrol's.
    """
    escalated = await events_of(hive, "alarm.escalated")
    return {e.subject_id for e in escalated if e.payload["kind"] == "PATROL_ANOMALY"}


async def _assert_every_entry_is_c2(hive: Hive) -> None:
    """Every kept observation is `C2` Bee Bread, and names exactly the Warden that kept it."""
    entries = await stored_entries(hive)
    assert entries
    assert all(
        entry.clearance is HoneyClearance.C2 and len(entry.ref_ids) == 1 for entry in entries
    )


# ──────────────────────────────────────────────────────────────────────────────
# A quiet interval records nothing notable
# ──────────────────────────────────────────────────────────────────────────────


def test_a_quiet_interval_records_nothing_notable_and_discards_what_it_covered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    watched = watched_directory(tmp_path)
    model = PatrolModel()
    hive = watching_hive(tmp_path, model, watched)
    writes = forbid_session_writes(monkeypatch)

    asyncio.run(_wait_for_a_quiet_patrol(hive, watched, model))

    assert writes == []


async def _wait_for_a_quiet_patrol(hive: Hive, watched: Path, model: PatrolModel) -> None:
    """The async body of the quiet scenario: nothing planted, one Patrol, then the checks."""
    stand = _stand_of(hive)
    async with run_hive(hive):
        watched_before = as_found(watched)
        await wait_until(lambda: patrolled(hive, "NOTHING"), timeout_s=WAIT_S)
        remaining = await kept_by(hive, stand)
        events = list(await hive.stores.trail.query(TrailQuery()))
        assert as_found(watched) == watched_before

    assert model.shown  # The model really was asked, and shown the machine's facts as data.
    assert "<<<watch observations untrusted>>>" in model.shown[0]
    assert not [e for e in events if e.kind == "alarm.raised"]
    first = next(e for e in events if e.kind == "warden.patrol")
    assert first.payload["outcome"] == "NOTHING"
    covered = first.payload["observations"]
    assert isinstance(covered, int) and covered >= 1
    # What the first Patrol covered is gone: whatever is left was kept after that Patrol ended.
    assert all(item.kept_at >= first.at for item in remaining)


# ──────────────────────────────────────────────────────────────────────────────
# Watching pauses while a real bee works
# ──────────────────────────────────────────────────────────────────────────────


def test_watching_pauses_while_a_real_bee_works_and_resumes_after(tmp_path: Path) -> None:
    model = PatrolModel(HaikuScript(default_worker_turn))
    hive = watching_hive(tmp_path, model, watched_directory(tmp_path))

    asyncio.run(_run_a_goal_while_watching(hive))


async def _run_a_goal_while_watching(hive: Hive) -> None:
    """The async body of the busy scenario: a goal runs between two spells of watching."""
    stand = _stand_of(hive)
    async with run_hive(hive):
        await wait_until(lambda: _has_kept(hive, stand), timeout_s=WAIT_S)
        report = await run_goal(hive, _HAIKU_GOAL, clearance=HoneyClearance.C1, timeout_s=WAIT_S)
        assert report.succeeded, report
        await wait_until(lambda: _watching_again(hive, stand), timeout_s=WAIT_S)
        busy = await _busy_spans(hive, stand)
        kept_at = [item.kept_at for item in await kept_by(hive, stand)]

    assert busy, "the Drone's task never moved the Warden to ACTIVE"
    for start, end in busy:
        # A sample kept in the middle of a task would mean the Warden watched while it worked.
        assert not [at for at in kept_at if start < at < end], (start, end)
    assert any(at > busy[-1][1] for at in kept_at)  # And it went back to watching afterwards.


async def _busy_spans(hive: Hive, warden_id: str) -> list[tuple[datetime, datetime]]:
    """Return the (ACTIVE, back to WATCH) spans of a Warden's life, from its trail edges."""
    spans: list[tuple[datetime, datetime]] = []
    opened: datetime | None = None
    for edge in await hive.stores.trail.query(TrailQuery(family="warden")):
        if edge.subject_id != warden_id:
            continue
        if edge.kind == "warden.active" and opened is None:
            opened = edge.at
        elif edge.kind == "warden.watch" and opened is not None:
            spans.append((opened, edge.at))
            opened = None
    return spans


async def _watching_again(hive: Hive, warden_id: str) -> bool:
    """Whether the Warden has settled back to WATCH after its task and kept a newer sample."""
    spans = await _busy_spans(hive, warden_id)
    if not spans:
        return False
    return any(item.kept_at > spans[-1][1] for item in await kept_by(hive, warden_id))


# ──────────────────────────────────────────────────────────────────────────────
# A device is watched through its packet
# ──────────────────────────────────────────────────────────────────────────────


def test_a_device_is_watched_through_its_packet_and_each_warden_keeps_only_its_own_samples(
    tmp_path: Path,
) -> None:
    hive = swarm_watching_hive(tmp_path, free_port(), PatrolModel())

    asyncio.run(_watch_a_device(hive, tmp_path))


async def _watch_a_device(hive: Hive, tmp_path: Path) -> None:
    """Enrol a laptop, let both Wardens watch, plant a hog both can see, and read what each kept."""
    assert hive.swarm is not None
    async with run_hive(hive):
        invite = await hive.swarm.door.mint_invite(
            AccessLevel.FULL, CombShieldLevel.MEADOW, label="e2e watched laptop"
        )
        device = await enrol(invite.invite_string, tmp_path / "device", hive.clock)
        node = await enrolled_node(hive)
        stand, laptop = _stand_of(hive), str(node.warden_id)
        async with running(device):
            await wait_until(lambda: _watching_idle(hive, node), timeout_s=WAIT_S)
            await wait_until(lambda: _has_kept(hive, stand, laptop), timeout_s=WAIT_S)
            leases_before = len(await _leases_of(hive, node))
            with planted_hog(tmp_path):
                for warden in (stand, laptop):
                    alarmed = partial(patrolled, hive, "ALARM", warden)
                    await wait_until(alarmed, timeout_s=WAIT_S)
            await _assert_each_warden_kept_its_own(hive, node, (stand, laptop))
            await _assert_watching_touched_nothing(hive, node, leases_before)
            for lease in await _leases_of(hive, node):
                scratch = device.layout.scratch(LeaseId(str(lease.payload["lease_id"])))
                assert not scratch.exists()  # The laptop holds nothing of the Hive's.
        events = await events_of(hive, "alarm.raised")
        assert [e.payload["kind"] for e in events].count("PATROL_ANOMALY") >= 2  # One per Warden.
        assert HOG_NAME not in everything_said(list(await hive.stores.trail.query(TrailQuery())))


async def _watching_idle(hive: Hive, node: SwarmNode) -> bool:
    """Whether the laptop's Warden is up, in WATCH, and holds no lease: idle, so it watches."""
    assert hive.swarm is not None
    served = hive.swarm.gate.wardens.get(node.device_id)
    if served is None:
        return False
    return served.warden.state is WardenState.WATCH and served.warden.lease is None


async def _leases_of(hive: Hive, node: SwarmNode) -> list[PheromoneEvent]:
    """Every lease the Hive has recorded on the laptop's Cell."""
    leased = await trail_events(hive, "cell.leased")
    return [event for event in leased if event.subject_id == node.cell_id]


async def _assert_each_warden_kept_its_own(
    hive: Hive, node: SwarmNode, wardens: tuple[str, str]
) -> None:
    """Each Warden's kept observations describe its own machine, and the hog is among them."""
    stand_device = device_id_of_cell(hive_stand_cell_id(hive.manifest.hive.node_id))
    expected = dict(zip(wardens, (stand_device, node.device_id), strict=True))
    assert stand_device != node.device_id
    for warden, device_id in expected.items():
        kept = await kept_by(hive, warden)
        assert kept
        assert {item.observation.device_id for item in kept} == {device_id}
        seen = [p for item in kept for p in item.observation.processes if p.name == HOG_NAME]
        assert seen, f"the hog was not seen through {warden}'s probe"
    await _assert_every_entry_is_c2(hive)


async def _assert_watching_touched_nothing(hive: Hive, node: SwarmNode, leases_before: int) -> None:
    """The laptop was watched without a lease and without a command: only the packet answered."""
    assert len(await _leases_of(hive, node)) == leases_before
    assert not await trail_events(hive, "swarm.command_sent")
