"""End-to-end: a Night Veil Cell reaches the Hive Stand only through Tor, and works (step 10.3a).

Roadmap step 10.3a's exit bullet, "a Night Veil Cell's Waggle transport may reach the Hive Stand
only through the Tor SOCKS proxy to its hidden-service address", run through a whole Hive: a
human's goal request naming NIGHT_VEIL is planned by the Queen, placed on a fresh Virtual Cell and
provisioned by the phase 5 suite's container-spawning fake backend, which runs a real in-Cell
Warden from the bootstrap the backend minted. That bootstrap names the tier, the Hive Stand's
onion service and the Tor SOCKS proxy, all from `[security.tiers.NIGHT_VEIL]`; the proxy is a
`FakeSocksProxy` playing Tor, and it is the only route from the onion name to the Hive Stand's
real `CellListener`. The Cell announces NIGHT_VEIL with its control-link checks passed, the Queen's
link to it carries that tier, and a Drone does the task's real work on it.

Two seams stand in for what no test host has. Tor itself is the fake proxy. The Queen's Night
Veil attestation probe is wired fail-closed in production until a Queen-side CellSession exists
(`hivemind.cli.compose.virtual_cells._fail_closed_night_veil_probe`, roadmap step 5.7b), so this
Hive's probe is the all-green `FakeNightVeilProbe` `test_night_veil_floors` uses too.

The binding is checked where it happens, on the Brood Chamber's own `assign`: the trail keeps
only the skeleton of a Night Veil task (codingrules section 12), so its `task.assigned` names no
Cell and no tier (`test_night_veil_boundary` covers the rest of that skeleton).

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests.unit.cli.in_cell.test_main_night_veil for the in-Cell entry point on its own.
    - tests.e2e.test_night_veil_floors for the Queen's side of a Night Veil request.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from builders.human import is_planning, make_goal_request
from builders.virtual_cells import (
    ContainerSpawningFakeCellBackend,
    single_haiku_plan,
    virtual_cells_manifest,
)
from e2e.kernel_helpers import (
    default_worker_turn,
    judge_approve_response,
    plan_response,
    wait_until,
)

from hivemind.brood_chamber import BroodChamber, Task, TaskFilter, TaskStatus, is_terminal
from hivemind.cell import Cell, CombShieldLevel
from hivemind.cli.compose import Hive, build_hive, run_hive
from hivemind.forage import ModelSlot
from hivemind.hive.models import NetworkPolicy
from hivemind.hive.night_veil import FakeNightVeilProbe, NightVeilProbe
from hivemind.llm import LLMRequest, LLMResponse, text_response
from hivemind.manifest import load_manifest
from hivemind.pheromone import TrailQuery
from waggle.clock import SystemClock
from waggle.ids import CellId, TaskId, WardenId
from waggle.transport.socks import FakeSocksProxy

pytestmark = pytest.mark.e2e

_ONION = "7jjm54ntxrtbp4fjhhw2gdk7zz2fshgnubimtmc5dcczncvdfo3lnbid.onion"  # A valid v3 address.
_ONION_PORT = 8710  # The hidden service's virtual port; the fake Tor maps it to the listener.
_TIMEOUT_S = 20.0  # Generous: the whole run finishes in a few seconds on loopback.
_DECISION = '{"action": "RECORD", "reason": "Nothing to decide."}'  # Any awake episode's answer.
_PLAN = single_haiku_plan("haiku_1.txt")


def _responder(request: LLMRequest) -> LLMResponse:
    """Answer the Hive Stand's one fake provider: the plan, any awake episode, and the Judge."""
    if request.slot is ModelSlot.QUEEN:
        return plan_response(request, _PLAN) if is_planning(request) else text_response(_DECISION)
    if request.slot is ModelSlot.JUDGE:
        return judge_approve_response(request)
    return default_worker_turn(request)


def _night_veil_manifest(tmp_path: Path, tor: FakeSocksProxy) -> Path:
    """The phase 5 suite's fake-backend manifest, plus a Night Veil tier profile using `tor`."""
    manifest_path = virtual_cells_manifest(tmp_path)
    profile = (
        "\n[security.tiers.NIGHT_VEIL]\n"
        'egress_profile = "vpn_tor"\ncontrol_channel = "tor_hidden_service"\n'
        f'hidden_service_address = "{_ONION}:{_ONION_PORT}"\ntor_socks = "{tor.url()}"\n'
        'locale_profile = "C.UTF-8"\n'
    )
    with manifest_path.open("a", encoding="utf-8") as handle:
        handle.write(profile)
    return manifest_path


def _green_probe(_cell: Cell) -> NightVeilProbe:
    """The attestation probe this Hive runs in place of production's fail-closed one."""
    return FakeNightVeilProbe()


@asynccontextmanager
async def _fake_tor() -> AsyncIterator[FakeSocksProxy]:
    """A loopback SOCKS port playing Tor; its one route is added once the listener is up."""
    tor = FakeSocksProxy()
    await tor.start()
    try:
        yield tor
    finally:
        await tor.close()


async def _build(manifest_path: Path) -> Hive:
    """Build the Hive off this loop: `build_hive` runs its own `asyncio.run` seams."""
    manifest = load_manifest(manifest_path, {})
    return await asyncio.to_thread(
        build_hive, manifest, environ={}, clock=SystemClock(), responders={"fake": _responder}
    )


async def _only_task(hive: Hive) -> Task | None:
    """The one task the request was planned into, once it exists."""
    tasks = await hive.stores.chamber.list(TaskFilter())
    return tasks[0] if tasks else None


async def _finished(hive: Hive) -> bool:
    task = await _only_task(hive)
    return task is not None and is_terminal(task.status)


def _record_bindings(
    chamber: BroodChamber, monkeypatch: pytest.MonkeyPatch
) -> list[tuple[CellId, CombShieldLevel]]:
    """Record every `(cell_id, bound_tier)` the Queen's dispatcher assigns a task to."""
    bindings: list[tuple[CellId, CombShieldLevel]] = []
    real_assign = chamber.assign

    async def assign(
        task_id: TaskId,
        warden_id: WardenId,
        cell_id: CellId,
        reason: str,
        *,
        bound_tier: CombShieldLevel,
    ) -> Task:
        bindings.append((cell_id, bound_tier))
        return await real_assign(task_id, warden_id, cell_id, reason, bound_tier=bound_tier)

    monkeypatch.setattr(chamber, "assign", assign)
    return bindings


def _virtual_cell(hive: Hive) -> Cell | None:
    """The Cell of the one Warden the Queen holds besides the Hive Stand's, once attached."""
    stand = hive.warden_link.cell.id
    return next((link.cell for link in hive.queen.wardens if link.cell.id != stand), None)


async def test_a_night_veil_cell_works_for_the_queen_over_tor_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "hivemind.cli.compose.virtual_cell_backends.FakeCellBackend",
        ContainerSpawningFakeCellBackend,
    )
    monkeypatch.setattr(
        "hivemind.cli.compose.virtual_cells._fail_closed_night_veil_probe", _green_probe
    )
    async with _fake_tor() as tor:
        hive = await _build(_night_veil_manifest(tmp_path, tor))
        bindings = _record_bindings(hive.stores.chamber, monkeypatch)
        assert hive.virtual_cells is not None
        backend = hive.virtual_cells.registry.get("fake")
        assert isinstance(backend, ContainerSpawningFakeCellBackend)
        try:
            async with run_hive(hive):
                # The hidden service answers on the Hive Stand's listener, as Tor would route it.
                listener_port = urlsplit(hive.virtual_cells.listener.uri).port
                assert listener_port is not None
                tor.routes[_ONION] = ("127.0.0.1", listener_port)
                request = make_goal_request(SystemClock(), comb_shield=CombShieldLevel.NIGHT_VEIL)
                await hive.queen.request_goal(request)
                await wait_until(lambda: _virtual_cell(hive) is not None, timeout_s=_TIMEOUT_S)
                # The Cell as the Queen holds it: built from the tier its CellReady announced.
                cell = _virtual_cell(hive)
                await wait_until(lambda: _finished(hive), timeout_s=_TIMEOUT_S)
        finally:
            await backend.aclose()

    task = await _only_task(hive)
    assert task is not None
    assert task.status is TaskStatus.SUCCEEDED
    # The Cell was provisioned for the tier, and dialled the Hive Stand by onion name through Tor.
    [spec] = backend.provision_calls
    assert spec.comb_shield is CombShieldLevel.NIGHT_VEIL
    assert spec.network_policy is NetworkPolicy.VPN_TOR
    assert tor.requests == [(_ONION, _ONION_PORT)]
    [attested] = await hive.stores.trail.query(TrailQuery(kind="cell.attested"))
    assert attested.payload["passed"] is True
    # It announced its own tier, so the Queen's link says NIGHT_VEIL and the task was bound to
    # NIGHT_VEIL: a Cell announcing MEADOW would have bound a Night Veil task to MEADOW.
    assert cell is not None
    assert cell.comb_shield is CombShieldLevel.NIGHT_VEIL
    assert bindings == [(cell.id, CombShieldLevel.NIGHT_VEIL)]
    [assigned] = await hive.stores.trail.query(TrailQuery(kind="task.assigned", subject_id=task.id))
    assert assigned.payload == {}
