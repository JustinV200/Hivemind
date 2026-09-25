"""End-to-end: a Drone lured in a Night Veil Cell; the Queen isolates it, and nothing outlives it.

Roadmap step 10.6 on the most sensitive tier, the whole chain with nothing stubbed but the model
and Tor. A human's goal request naming NIGHT_VEIL is planned by the Queen and placed on a fresh
Virtual Cell the container-spawning fake backend provisions, reached through a fake Tor exactly as
`tests.e2e.test_night_veil_boundary` builds one. Its real in-Cell Warden runs the lured Drone
(`builders.guard_bee.lure_script`): outside text flagged by the Cell's scanner, then a host it
holds no `net` capability for refused, then a long command.

Everything about a living Night Veil Cell lives only in its ephemeral segment on the Queen's side:
its Warden's shipped records, the Queen's own records about it, and the Guard Bee's alert. So the
composed Guard Bee reads that segment beside the durable trail, correlates the flag and the refusal
in that one episode, and files its request through the Queen's door. The rule is a shipped dire
pattern, so she isolates the Cell by rule. The segment holds the whole story while the Cell lives;
once the Hive stops and the Cell is torn down, the segment is purged with it, the Guard Bee's alert
among its records, and the durable trail holds nothing about the Cell or its task but the skeleton
codingrules section 12 names. The isolation writes no Cell Wax (a note and its `memory.wax_*`
records would outlive the Cell): while the Cell lives, its isolation stands in its own segment,
where the Queen reads it back, and no placement is ever offered the Cell.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests.e2e.test_guard_bee_on_virtual_cell for the same lure on a MEADOW Virtual Cell.
    - tests.e2e.test_night_veil_boundary for the boundary itself, on every end path.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from builders.guard_bee import LuredCells, quick_rounds, ship_on_every_call
from builders.human import is_planning, make_goal_request
from builders.virtual_cells import single_haiku_plan, virtual_cells_manifest
from e2e.kernel_helpers import (
    default_worker_turn,
    judge_approve_response,
    plan_response,
    wait_until,
)

from hivemind.cell import Cell, CombShieldLevel, HoneyClearance
from hivemind.cli.compose import Hive, build_hive, run_hive
from hivemind.forage import ModelSlot
from hivemind.hive.night_veil import FakeNightVeilProbe, NightVeilProbe
from hivemind.llm import LLMRequest, LLMResponse, text_response
from hivemind.manifest import load_manifest
from hivemind.memory import WaxState
from hivemind.pheromone import (
    MAX_QUERY_LIMIT,
    SKELETON_KINDS,
    EphemeralSegments,
    PheromoneEvent,
    TrailQuery,
    skeleton_event,
)
from hivemind.queen.dispatcher.snapshot import build_inventory
from hivemind.queen.isolation import IsolationState, read_isolation
from waggle.clock import SystemClock
from waggle.ids import CellId
from waggle.transport.socks import FakeSocksProxy

pytestmark = pytest.mark.e2e

_ONION = "7jjm54ntxrtbp4fjhhw2gdk7zz2fshgnubimtmc5dcczncvdfo3lnbid.onion"  # A valid v3 address.
_ONION_PORT = 8710  # The hidden service's virtual port; the fake Tor maps it to the listener.
_WAIT_S = 30.0  # Each wait: the Cell boots over the fake Tor and works within a few seconds.
_DECISION = '{"action": "RECORD", "reason": "Nothing to decide."}'  # Any awake episode's answer.
_PLAN = single_haiku_plan("haiku_1.txt")
_EVERYTHING = TrailQuery(limit=MAX_QUERY_LIMIT)
_REQUEST_RECORDS = "queen.goal_request_"  # The human's own request records, not the Cell's work.
_PURGED = "cell.purged"
_CELL_WAX = "memory.wax_"  # A Cell Wax note's own records: none may name a Night Veil Cell.
_ALARM_ESCALATED = "alarm.escalated"  # The SECURITY Alarm's own row (see its assertion below).
_ID_PREFIXES = ("task_", "worker_", "warden_", "grant_", "cell_")  # What the story's ids look like.
# What the whole story is, inside the segment: the Cell's own records and the Queen's about it.
_STORY = ("guard.injection_suspected", "guard.denied", "guard.alert", "queen.decided")

Snapshot = tuple[PheromoneEvent, ...]


def _responder(request: LLMRequest) -> LLMResponse:
    """The Hive Stand's fake provider: the plan, any awake episode, and the Judge."""
    if request.slot is ModelSlot.QUEEN:
        return plan_response(request, _PLAN) if is_planning(request) else text_response(_DECISION)
    if request.slot is ModelSlot.JUDGE:
        return judge_approve_response(request)
    return default_worker_turn(request)


def _green_probe(_cell: Cell) -> NightVeilProbe:
    """The attestation probe this Hive runs in place of production's fail-closed one."""
    return FakeNightVeilProbe()


def _manifest(tmp_path: Path, tor: FakeSocksProxy) -> Path:
    """The Virtual side's fake-backend manifest, a Night Veil profile over `tor`, quick rounds."""
    manifest_path = virtual_cells_manifest(tmp_path)
    profile = (
        "\n[security.tiers.NIGHT_VEIL]\n"
        'egress_profile = "vpn_tor"\ncontrol_channel = "tor_hidden_service"\n'
        f'hidden_service_address = "{_ONION}:{_ONION_PORT}"\ntor_socks = "{tor.url()}"\n'
        'locale_profile = "C.UTF-8"\n'
    )
    with manifest_path.open("a", encoding="utf-8") as handle:
        handle.write(profile)
    return quick_rounds(manifest_path)


def _segments(hive: Hive) -> EphemeralSegments:
    """The Hive's Night Veil segments, behind the trail its Queen records through."""
    assert hive.virtual_cells is not None
    return hive.virtual_cells.night_veil.segments


async def _isolated_cell(hive: Hive) -> CellId | None:
    """The held Night Veil Cell whose segment records its isolation, if one does yet."""
    segments = _segments(hive)
    for cell in segments.held_cells():
        if await segments.query(cell, TrailQuery(kind="cell.isolated")):
            return cell
    return None


def _snapshot_teardown(hive: Hive, monkeypatch: pytest.MonkeyPatch) -> dict[CellId, Snapshot]:
    """Snapshot a held segment the moment its Cell's teardown starts, before the purge takes it."""
    assert hive.virtual_cells is not None
    lifecycle = hive.virtual_cells.lifecycle
    real_teardown = lifecycle.teardown
    seen: dict[CellId, Snapshot] = {}

    async def teardown(cell_id: CellId) -> None:
        if _segments(hive).holds(cell_id):
            seen[cell_id] = await _segments(hive).query(cell_id, _EVERYTHING)
        await real_teardown(cell_id)

    monkeypatch.setattr(lifecycle, "teardown", teardown)
    return seen


async def _scenario(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Hive, CellId, Snapshot]:
    """Run the lured Night Veil goal until the Queen isolates its Cell, then stop the Hive."""
    tor = FakeSocksProxy()
    await tor.start()
    try:
        manifest = load_manifest(_manifest(tmp_path, tor), {})
        # Off this loop: `build_hive` runs its own `asyncio.run` seams.
        hive = await asyncio.to_thread(
            build_hive, manifest, environ={}, clock=SystemClock(), responders={"fake": _responder}
        )
        seen = _snapshot_teardown(hive, monkeypatch)
        cell = await _run_until_isolated(hive, tor)
    finally:
        await tor.close()
    return hive, cell, seen[cell]


async def _run_until_isolated(hive: Hive, tor: FakeSocksProxy) -> CellId:
    """Run the Hive, request the goal at NIGHT_VEIL, and wait for the Cell's isolation."""
    backend = hive.virtual_cells.registry.get("fake") if hive.virtual_cells else None
    assert isinstance(backend, LuredCells)
    try:
        async with run_hive(hive):
            assert hive.virtual_cells is not None
            port = urlsplit(hive.virtual_cells.listener.uri).port
            assert port is not None
            tor.routes[_ONION] = ("127.0.0.1", port)  # As Tor would, once the listener is bound.
            request = make_goal_request(SystemClock(), comb_shield=CombShieldLevel.NIGHT_VEIL)
            await hive.queen.request_goal(request)
            await wait_until(lambda: _is_isolated(hive), timeout_s=_WAIT_S)
            cell = await _isolated_cell(hive)
            assert cell is not None
            await _assert_held_with_no_wax(hive, cell)
    finally:
        await backend.aclose()
    return cell


async def _is_isolated(hive: Hive) -> bool:
    """Whether a Night Veil Cell's segment records its isolation yet."""
    return await _isolated_cell(hive) is not None


async def _assert_held_with_no_wax(hive: Hive, cell: CellId) -> None:
    """While the isolated Cell lives: read back from its segment, never placed on, no Cell Wax."""
    deps = hive.queen._deps
    assert (await read_isolation(deps, cell)).state is IsolationState.ISOLATED
    assert cell in {link.cell.id for link in hive.queen.wardens}  # Its Warden is still attached.
    inventory = await build_inventory(deps, hive.queen.wardens)
    assert cell not in {candidate.cell_id for candidate in inventory.real}
    assert await hive.stores.memory.list_wax(cell, frozenset(WaxState), HoneyClearance.C2) == ()
    durable = await hive.stores.trail.query(_EVERYTHING)
    assert [e.kind for e in durable if e.kind.startswith(_CELL_WAX)] == []


def _assert_the_story_was_in_the_segment(hive: Hive, cell: CellId, snapshot: Snapshot) -> None:
    """While the Cell lived, its segment held the lure, the Guard Bee's alert and her decision."""
    kinds = {event.kind for event in snapshot}
    assert set(_STORY) <= kinds
    stand = hive.manifest.hive.node_id
    [flag] = [e for e in snapshot if e.kind == "guard.injection_suspected"]
    assert flag.node_id != stand  # Recorded inside the Cell, shipped into its segment.
    [alert] = [e for e in snapshot if e.kind == "guard.alert"]
    assert (alert.payload["rule"], alert.payload["disposition"]) == (
        "injection_then_denial",
        "filed",
    )
    assert alert.payload["cell_id"] == cell
    [decided] = [e for e in snapshot if e.kind == "queen.decided"]
    assert (decided.payload["action"], decided.payload["basis"]) == ("ISOLATE_CELL", "rule")
    [isolated] = [e for e in snapshot if e.kind == "cell.isolated"]
    assert (
        isolated.payload["report_id"] == alert.payload["report_id"] == decided.payload["report_id"]
    )


async def _assert_only_the_skeleton_survives(hive: Hive, cell: CellId, snapshot: Snapshot) -> None:
    """After the purge: no segment, and nothing durable about the Cell but its skeleton."""
    assert not _segments(hive).holds(cell)
    assert await _segments(hive).query(cell, _EVERYTHING) == ()
    durable = await hive.stores.trail.query(_EVERYTHING)
    assert [e for e in durable if e.kind == "guard.alert"] == []  # Purged with the Cell.
    # Every id the Cell's story names, and the Cell's own nodes: nothing durable may name them
    # beyond the skeleton (the human's own request records aside, which name only the goal).
    ids = {cell}.union(*(_names(event) for event in snapshot if event.kind in _STORY))
    nodes = {event.node_id for event in snapshot} - {hive.manifest.hive.node_id}
    about = [
        e
        for e in durable
        if not e.kind.startswith(_REQUEST_RECORDS)
        and (e.node_id in nodes or ids & {e.subject_id, *_strings(e)})
    ]
    assert about, "The Cell's skeleton itself should be on the durable trail."
    assert not {e.kind for e in about} & {*_STORY, "cell.isolated"}  # None of the story.
    assert await hive.stores.memory.list_wax(cell, frozenset(WaxState), HoneyClearance.C2) == ()
    # Nor does anything else name the Cell anywhere in its words (a memory label's reason, say),
    # but for the isolation's SECURITY Alarm, whose `alarm.escalated` detail names the Cell: a
    # boundary gap in the Alarm's own record (and its chat line), outside isolation, reported apart.
    kept = SKELETON_KINDS | {_PURGED}
    named = {e.kind for e in durable if cell in json.dumps(e.payload) and e.kind not in kept}
    assert named <= {_ALARM_ESCALATED}, named
    for event in about:
        assert event.kind in kept, event.kind
        if event.kind in SKELETON_KINDS:
            cut = skeleton_event(event)
            assert cut is not None and cut.payload == event.payload, event.kind


def _names(event: PheromoneEvent) -> set[str]:
    """Every id an event of the Cell's story names: its subject, and its bees, task and grant."""
    return {event.subject_id} | {
        value for value in _strings(event) if value.startswith(_ID_PREFIXES)
    }


def _strings(event: PheromoneEvent) -> set[str]:
    """Every top-level string value in an event's payload."""
    return {value for value in event.payload.values() if isinstance(value, str)}


def test_a_lured_drone_in_a_night_veil_cell_is_isolated_and_nothing_outlives_the_cell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("hivemind.cli.compose.virtual_cell_backends.FakeCellBackend", LuredCells)
    monkeypatch.setattr(
        "hivemind.cli.compose.virtual_cells._fail_closed_night_veil_probe", _green_probe
    )
    ship_on_every_call(monkeypatch)  # The Cell's records reach its segment at once.

    hive, cell, snapshot = asyncio.run(_scenario(tmp_path, monkeypatch))

    _assert_the_story_was_in_the_segment(hive, cell, snapshot)
    asyncio.run(_assert_only_the_skeleton_survives(hive, cell, snapshot))
