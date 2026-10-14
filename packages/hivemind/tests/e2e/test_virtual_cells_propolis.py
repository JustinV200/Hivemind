"""End-to-end tests for Virtual Cells: Propolis, at the provider level and through a running Queen.

`.claude/roadmap.md`, phase 11's exit criteria: "On a Virtual Cell the same checks run against the
image, with Tor disabled for that tier." Beside `test_virtual_cells_night_veil.py` (the Night Veil
tier's version of the same contract): a PROPOLIS Cell is attested before it is ready, by the Real
Cell's tunnel checks and Tor's absence, and is otherwise refused. The first half proves that at the
provider -- ready only after a passed attestation, torn down after a red one or when no probe is
configured, the event naming its tier, MEADOW unattested and NIGHT_VEIL unchanged -- and feeds the
environment a backend renders for the Cell back through the Cell's own configuration, so the two
halves of the tier are shown to agree. The second half drives the Queen's own planning and
dispatch over the same real provider, lifecycle and gate: a task that needs PROPOLIS runs only on a
Cell whose attestation passed, and a Cell that failed it is torn down and never assigned a task.
The Virtual side lists a single spec, at PROPOLIS; matching a task's tier to a spec's is
placement's business and is not what this proves.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - test_virtual_cells_night_veil for the Night Veil criteria this mirrors.
    - test_night_veil_floors for the Queen-driven shape the second half follows.
    - tests/unit/queen/cell_gate/test_provider_propolis.py for the unit-level cases.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from builders.attested_cells import (
    ProviderRig,
    RigWorld,
    make_provider_rig,
    make_tier_spec,
)
from builders.cells import make_identity
from builders.human import make_goal_request, queen_responder, single_task_plan, wait_until
from builders.queen import make_queen_deps

from hivemind.brood_chamber import Task, TaskFilter, TaskStatus
from hivemind.cell import Cell, CombShieldLevel
from hivemind.cli.in_cell.config import build_runtime_config
from hivemind.hive import CellProvisionError
from hivemind.hive.attestation import PropolisProbeFactory
from hivemind.hive.backends.bootstrap import QueenEndpoint
from hivemind.hive.cell_state import VirtualCellStatus
from hivemind.hive.models import VirtualCellSpec
from hivemind.hive.night_veil import CheckResult, CheckStatus
from hivemind.hive.propolis import CHECK_TOR, PROPOLIS_CHECK_NAMES, FakePropolisProbe, PropolisProbe
from hivemind.llm import FakeLLMProvider
from hivemind.manifest.env import read_in_cell_env
from hivemind.pheromone import PheromoneEvent, PheromoneTrail, TrailQuery
from hivemind.queen.deps import QueenDeps
from hivemind.queen.placement import PlacementPolicy, ProvisionVirtual, VirtualBackendCandidate
from hivemind.queen.queen import Queen
from waggle.clock import FakeClock
from waggle.ids import new_node_id
from waggle.signing import Ed25519Signer

pytestmark = pytest.mark.e2e

_QUEEN_URL = "ws://host.docker.internal:8710"  # The ordinary endpoint a Cell dials out to.
_DECISION: dict[str, object] = {"action": "RECORD", "reason": "Nothing to decide."}
_PROPOLIS_NEEDS: dict[str, object] = {"comb_shield": "PROPOLIS", "isolation": "REQUIRED"}
_TOR_RUNS = CheckResult(status=CheckStatus.FAIL, detail="a process named tor is running")


def _task() -> Task:
    """A Task stand-in; `LifecycleVirtualCellProvider.acquire` never reads it for these tiers."""
    return None  # type: ignore[return-value]


def _green(_cell: Cell) -> PropolisProbe:
    """A PROPOLIS probe factory whose Cells pass every check."""
    return FakePropolisProbe()


def _tor_runs(_cell: Cell) -> PropolisProbe:
    """A PROPOLIS probe factory whose Cells are found running Tor."""
    return FakePropolisProbe({CHECK_TOR: _TOR_RUNS})


def _placement(tier: CombShieldLevel = CombShieldLevel.PROPOLIS) -> ProvisionVirtual:
    """A decision to provision one Cell at `tier` on the rig's fake backend."""
    return ProvisionVirtual(spec=make_tier_spec(tier), backend="fake", reason="test")


async def _kinds(trail: PheromoneTrail) -> list[str]:
    """Every event kind on a trail, in the order it was recorded."""
    return [event.kind for event in await trail.query(TrailQuery())]


# ──────────────────────────────────────────────────────────────────────────────
# The provider: ready only after a passed attestation.
# ──────────────────────────────────────────────────────────────────────────────


async def test_a_propolis_cell_is_ready_only_after_a_passed_attestation() -> None:
    rig = make_provider_rig(propolis=_green)

    link = await rig.provider.acquire(_placement(), _task())

    assert rig.lifecycle.status_of(link.cell.id) is VirtualCellStatus.READY
    kinds = await _kinds(rig.trail)
    # Provisioned, then looked at, then ready: the attestation sits between, never after.
    assert (
        kinds.index("cell.provisioned") < kinds.index("cell.attested") < kinds.index("cell.ready")
    )
    [attested] = await rig.trail.query(TrailQuery(kind="cell.attested"))
    assert attested.payload["passed"] is True
    assert attested.payload["comb_shield"] == "PROPOLIS"
    assert set(PROPOLIS_CHECK_NAMES) <= set(attested.payload)


async def test_a_propolis_cell_is_torn_down_after_a_red_attestation() -> None:
    rig = make_provider_rig(propolis=_tor_runs)

    with pytest.raises(CellProvisionError, match=CHECK_TOR):
        await rig.provider.acquire(_placement(), _task())

    kinds = await _kinds(rig.trail)
    assert "cell.ready" not in kinds
    assert kinds.index("cell.attested") < kinds.index("cell.destroyed")
    [attested] = await rig.trail.query(TrailQuery(kind="cell.attested"))
    assert attested.payload["passed"] is False  # Recorded though it lost: the trail says why.
    assert attested.payload["red"] == [CHECK_TOR]
    assert rig.lifecycle.live_cells() == ()  # Torn down: no Cell of it is left running.


async def test_a_hive_with_no_propolis_probe_readies_no_propolis_cell() -> None:
    rig = make_provider_rig()  # The composition root's own default, which no session path lifts.

    with pytest.raises(CellProvisionError, match="no PROPOLIS attestation probe"):
        await rig.provider.acquire(_placement(), _task())

    assert "cell.ready" not in await _kinds(rig.trail)


async def test_meadow_stays_unattested_and_night_veil_is_attested_as_it_always_was() -> None:
    rig = make_provider_rig(propolis=_tor_runs)  # Would fail a PROPOLIS Cell, if asked.

    meadow = await rig.provider.acquire(_placement(CombShieldLevel.MEADOW), _task())
    night_veil = await rig.provider.acquire(_placement(CombShieldLevel.NIGHT_VEIL), _task())

    assert rig.lifecycle.status_of(meadow.cell.id) is VirtualCellStatus.READY
    assert rig.lifecycle.status_of(night_veil.cell.id) is VirtualCellStatus.READY
    [attested] = await rig.trail.query(TrailQuery(kind="cell.attested"))  # Only Night Veil's.
    assert attested.subject_id == night_veil.cell.id
    assert attested.payload["comb_shield"] == "NIGHT_VEIL"


async def test_what_the_backend_renders_for_a_propolis_cell_is_what_the_cell_accepts(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    endpoint = QueenEndpoint(
        waggle_url=_QUEEN_URL,
        queen_node_id=new_node_id(clock),
        queen_verify_key_hex=Ed25519Signer.generate().public_key_bytes.hex(),
    )
    rig = make_provider_rig(propolis=_green, endpoint=endpoint)
    link = await rig.provider.acquire(_placement(), _task())

    environment = rig.backend.bootstraps[link.cell.id].environment()
    config = build_runtime_config(
        read_in_cell_env({**environment, "HIVEMIND_SCRATCH_ROOT": str(tmp_path)}), clock
    )

    # The ordinary endpoint: no onion URL and no SOCKS proxy, the tier announced as PROPOLIS.
    assert environment["HIVEMIND_COMB_SHIELD"] == "PROPOLIS"
    assert "HIVEMIND_SOCKS_PROXY_URL" not in environment
    assert config.queen_waggle_url == _QUEEN_URL
    assert config.socks_proxy_url is None
    assert config.spawn_config.comb_shield is CombShieldLevel.PROPOLIS


# ──────────────────────────────────────────────────────────────────────────────
# Through a running Queen: a Propolis task is placed only on a Cell that passed.
# ──────────────────────────────────────────────────────────────────────────────


def _propolis_plan(goal: str) -> dict[str, object]:
    """A one-task plan whose task needs the PROPOLIS tier and a Cell of its own."""
    plan = single_task_plan(goal)
    tasks = plan["tasks"]
    assert isinstance(tasks, list)
    tasks[0]["needs"] = _PROPOLIS_NEEDS
    return plan


def _candidate(deps: QueenDeps, rig: ProviderRig) -> VirtualBackendCandidate:
    """The rig's backend as placement sees it, listing one spec at the PROPOLIS tier."""
    spec: VirtualCellSpec = make_tier_spec(CombShieldLevel.PROPOLIS, deps.identity.hive_id)
    return VirtualBackendCandidate(
        name=rig.backend.name, capabilities=rig.backend.capabilities, specs=(spec,)
    )


def _queen(probe: PropolisProbeFactory) -> tuple[Queen, QueenDeps, ProviderRig]:
    """A Queen whose Virtual side is the real provider over a gated fake backend and `probe`."""
    llm = FakeLLMProvider(responder=queen_responder(_DECISION, [], _propolis_plan))
    base, _link, _end = make_queen_deps(fake_provider=llm)
    queens_own = make_identity(
        base.clock, hive_id=base.identity.hive_id, node_id=base.identity.node_id
    )
    world = RigWorld(base.clock, base.trail, queens_own)
    rig = make_provider_rig(propolis=probe, world=world)
    deps = dataclasses.replace(
        base,
        virtual_provider=rig.provider,
        virtual_backends=(_candidate(base, rig),),
        placement_policy=PlacementPolicy(prefer="virtual"),
        in_process_providers=frozenset({"fake"}),
    )
    queen = Queen(deps)
    rig.provider.bind_queen(queen)  # The Queen's own wardens, now; the rig bound a stub before.
    rig.backend.attach = queen.attach_warden
    return queen, deps, rig


async def _run_until(queen: Queen, done: Callable[[], Awaitable[bool]]) -> None:
    """Run the Queen's own loop until `done()`, then stop her cleanly."""
    loop = asyncio.ensure_future(queen.run())
    try:
        await wait_until(done)
    finally:
        await queen.stop()
        await asyncio.wait_for(loop, timeout=5.0)


async def _tasks(deps: QueenDeps) -> tuple[Task, ...]:
    """Every task in the Queen's Brood Chamber."""
    return tuple(await deps.chamber.list(TaskFilter()))


async def _events(deps: QueenDeps, kind: str) -> list[PheromoneEvent]:
    """Every event of `kind` on the Queen's trail, in trail order."""
    return list(await deps.trail.query(TrailQuery(kind=kind)))


async def test_a_propolis_task_runs_on_a_cell_that_passed_its_attestation() -> None:
    queen, deps, _rig = _queen(_green)
    await queen.request_goal(make_goal_request(deps.clock))

    async def running() -> bool:
        tasks = await _tasks(deps)
        return bool(tasks) and tasks[0].status is TaskStatus.RUNNING

    await _run_until(queen, running)

    [task] = await _tasks(deps)
    assert task.bound_tier is CombShieldLevel.PROPOLIS  # Bound to the tier of the Cell it is on.
    [attested] = await _events(deps, "cell.attested")
    [assigned] = await _events(deps, "queen.assigned")
    assert attested.payload["passed"] is True
    assert assigned.payload["cell_id"] == attested.subject_id  # The very Cell that was attested.
    kinds = [event.kind for event in await deps.trail.query(TrailQuery())]
    assert kinds.index("cell.attested") < kinds.index("cell.ready") < kinds.index("queen.assigned")


async def _placement_failures(deps: QueenDeps) -> list[PheromoneEvent]:
    """The rows in which the Queen says a task found no Cell, each with the reason it gave."""
    decided = await _events(deps, "queen.decided")
    return [event for event in decided if event.payload.get("reason") == "placement_failed"]


async def test_a_propolis_task_is_never_assigned_to_a_cell_that_failed_its_attestation() -> None:
    queen, deps, rig = _queen(_tor_runs)
    await queen.request_goal(make_goal_request(deps.clock))

    async def refused() -> bool:
        return bool(await _placement_failures(deps))

    await _run_until(queen, refused)

    [attested, *_rest] = await _events(deps, "cell.attested")
    assert attested.payload["passed"] is False
    assert attested.payload["red"] == [CHECK_TOR]
    # The Queen ran on past the refusal and found no Cell for the task (she says so on the trail,
    # which is what `refused` waited for): torn down and never ready, so nothing was assigned.
    assert await _events(deps, "cell.destroyed") != []
    assert await _events(deps, "cell.ready") == []
    assert await _events(deps, "queen.assigned") == []
    assert all(task.status is TaskStatus.PENDING for task in await _tasks(deps))
    assert all(cell.status is not VirtualCellStatus.READY for cell in rig.lifecycle.live_cells())
