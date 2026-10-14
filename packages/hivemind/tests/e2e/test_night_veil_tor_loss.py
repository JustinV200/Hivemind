"""End-to-end: Tor dies under a working Night Veil Cell, which clusters and never falls back.

Phase 11's exit bullet, run through a whole Hive: "killing the Tor daemon while leaving the VPN and
default route up clusters the Cell instead of falling back to a direct or VPN-only connection".
A human's Night Veil goal is placed on a fresh Virtual Cell whose real in-Cell Warden reaches the
Hive Stand's listener through a fake Tor (`tests.e2e.night_veil_hive`), and its Worker is held at
work. Then Tor dies: the proxy is closed, and the relayed connection with it, while the listener
the Cell could reach directly stays right there on the same loopback, as the VPN and the default
route do on a real Cell. The Cell's Warden goes cut off, and past the Hive's `[supervision]
max_offline_s` (which the Queen told the Cell at provisioning) clusters fully, the task it was
working held at its Handoff; the Queen, whose end of the connection ended, holds no link to it; and
for the whole time nothing of the Cell reaches the Hive Stand by any other route: it is not
attached again, and no second Cell is provisioned for its task. Tor is then restarted on the port
its clients were given, and the same Warden dials back through it, by the onion name, and the
Queen takes it back as a Warden she knew.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests.unit.cli.in_cell.test_main_night_veil_redial for the same loss at the entry point.
    - tests.e2e.test_night_veil_link for the Cell's Tor link while it works.
    - hivemind.cli.in_cell.redial for the link that mends.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from builders.virtual_cells import VirtualCellsTuning
from e2e.kernel_helpers import wait_until
from e2e.night_veil_hive import (
    EVERYTHING,
    ONION,
    ONION_PORT,
    NightVeilRun,
    night_veil_hive,
    request,
    running,
    tasks,
    working,
)

from hivemind.brood_chamber import TaskStatus
from hivemind.pheromone import TrailQuery
from hivemind.wardens.deps import WardenDeps
from hivemind.wardens.offline import Outage
from waggle.ids import TaskId
from waggle.transport.socks import FakeSocksProxy

pytestmark = pytest.mark.e2e

_TIMEOUT_S = 20.0  # Generous: the whole run finishes in a few seconds on loopback.
_LIMIT_S = 2.0  # The Hive's `[supervision] max_offline_s`: short, so the Cell clusters in the test.
_QUIET_S = 1.0  # How long Tor stays dead after the Cell has clustered: nothing may get through.


def _attached(run: NightVeilRun) -> bool:
    """Whether the Queen holds a link to a Warden besides the Hive Stand's own: a Cell's."""
    stand = run.hive.warden_link.cell.id
    return any(link.cell.id != stand for link in run.hive.queen.wardens)


def _outage(deps: WardenDeps) -> Outage:
    """The Outage a cut-off Cell's Warden keeps; this Cell's Warden must have one."""
    assert deps.device is not None
    assert deps.device.outage is not None
    return deps.device.outage


async def _assert_clustered(run: NightVeilRun, deps: WardenDeps, task_id: TaskId) -> None:
    """Tor is dead: the Cell is cut off, then clusters, and gets nowhere by any other route."""
    outage = _outage(deps)
    # The Queen's end of the connection ended; the Cell noticed and was cut off; and past the limit
    # it clustered, the task it was working held at its Handoff.
    await wait_until(lambda: not _attached(run), timeout_s=_TIMEOUT_S)
    await wait_until(lambda: outage.cut_since is not None, timeout_s=_TIMEOUT_S)
    await wait_until(lambda: outage.is_clustered_fully, timeout_s=_TIMEOUT_S)
    assert [held.assignment.task_id for held in outage.held()] == [task_id]
    kinds = {event.kind for event in await deps.trail.query(EVERYTHING)}
    assert {"warden.offline", "warden.clustered"} <= kinds
    # And it got nowhere by any other route: not attached, no second Cell for its task, and the
    # Queen did not tear down the one that clusters.
    await asyncio.sleep(_QUIET_S)
    assert not _attached(run)
    assert len(run.backend.provision_calls) == 1
    assert run.backend.destroy_calls == []


async def _recorded_by_the_queen(run: NightVeilRun, kind: str) -> bool:
    """Whether the Queen has recorded a `kind` event about the Cell, in its segment."""
    [cell_id] = run.night_veil.segments.held_cells()
    events = await run.night_veil.segments.query(cell_id, TrailQuery(kind=kind))
    return any(event.node_id == run.hive.manifest.hive.node_id for event in events)


async def _assert_returned(run: NightVeilRun, deps: WardenDeps) -> None:
    """Tor is back: the Queen holds the Cell's Warden again, which left its outage."""
    outage = _outage(deps)
    await wait_until(lambda: _attached(run), timeout_s=_TIMEOUT_S)
    await wait_until(lambda: outage.cut_since is None, timeout_s=_TIMEOUT_S)
    # She takes it back as a Warden she knew: her own record, made after the link is attached.
    await wait_until(
        lambda: _recorded_by_the_queen(run, "warden.reconnected"), timeout_s=_TIMEOUT_S
    )


async def test_killing_tor_under_a_night_veil_cell_clusters_it_and_it_returns_over_tor_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tuning = VirtualCellsTuning(max_offline_s=_LIMIT_S)
    returned: FakeSocksProxy | None = None
    async with night_veil_hive(tmp_path, monkeypatch, hold_after=1, tuning=tuning) as run:
        try:
            async with running(run):
                await request(run)
                await wait_until(lambda: working(run), timeout_s=_TIMEOUT_S)
                [deps] = run.in_cell
                [task] = await tasks(run)
                assert _attached(run)
                port, routes = run.tor.port, dict(run.tor.routes)

                await run.tor.close()  # Tor dies; nothing is done to the listener beside it.
                await _assert_clustered(run, deps, task.id)

                returned = FakeSocksProxy(routes)
                await returned.start(port=port)  # Tor is back, on the port its clients hold.
                await _assert_returned(run, deps)
        finally:
            if returned is not None:
                await returned.close()

    # The Warden came back by the onion name, through the restarted Tor; the dead one had served
    # only the Cell's first connection.
    assert returned is not None and returned.requests == [(ONION, ONION_PORT)]
    assert run.tor.requests == [(ONION, ONION_PORT)]
    [after] = await tasks(run)
    assert after.status is not TaskStatus.SUCCEEDED  # It was clustered, not carried on elsewhere.
