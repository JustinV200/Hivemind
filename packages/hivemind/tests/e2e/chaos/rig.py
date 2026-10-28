"""Hold every chaos scenario to the same bar: a bounded run, then nothing left open.

Roadmap 13.6: "Every goal finishes or fails explicitly; nothing hangs; no lease or grant is left
open." `bounded` is the "nothing hangs" half: every scenario's whole run goes through it, so a
wedge fails the test with the scenario's name instead of stalling the nightly job. The rest is the
"nothing left open" half, read three ways: from the trail alone (`open_leases`, `open_grants`,
pure, so the Docker and Nuc legs can judge a trail they only hold as events), from the Queen's own
Forage ledger, and from the Virtual Cell backends, which must hold no Cell once the Hive is down.
`hive_on` builds a Hive from a written manifest with one responder for every fake provider, the
way every whole-Hive scenario in this tree does.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the `test_chaos_*`
    modules beside it.

Key invariants:
    - Judged after `run_hive` has returned: a Hive that is still up may rightly hold its Hive
      Stand's lease and the grants of work in flight.
    - A Virtual Cell's own lease is not a lease to give back (the Cell is destroyed from outside,
      `hivemind.cli.readback.virtual_offline.open_real_leases`), so it is never counted open.

See Also:
    - .claude/roadmap.md step 13.6 for the suite these hold to one bar.
    - hivemind.cli.abscond.scope, which reads the same three sources for Absconding.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Iterable
from pathlib import Path

from hivemind.brood_chamber import TaskFilter, TaskStatus
from hivemind.cli.compose import Hive, build_hive
from hivemind.cli.readback.virtual_offline import list_all_virtual
from hivemind.llm import Responder
from hivemind.manifest import load_manifest
from hivemind.pheromone import MAX_QUERY_LIMIT, PheromoneEvent, TrailQuery
from waggle.clock import SystemClock

HANG_S = 90.0  # The most any one scenario may take; each finishes in seconds when healthy.
_LEASED, _RELEASED = "cell.leased", "cell.released"
_GRANTED = "forage.granted"
_GRANT_ENDS = frozenset({"forage.revoked", "forage.expired"})  # A grant's terminal records.
_TERMINAL = frozenset({TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED})

__all__ = [
    "HANG_S",
    "assert_explicit_ends",
    "assert_nothing_left_open",
    "bounded",
    "hive_on",
    "open_grants",
    "open_leases",
]


def hive_on(manifest_path: Path, responder: Responder) -> Hive:
    """Build a Hive over `manifest_path`, every fake provider answering through `responder`.

    Builds outside any event loop (`build_hive` runs its own), so call it from sync code or
    through `asyncio.to_thread`.
    """
    manifest = load_manifest(manifest_path, {})
    return build_hive(manifest, environ={}, clock=SystemClock(), responders={"fake": responder})


async def bounded[ResultT](scenario: str, run: Awaitable[ResultT]) -> ResultT:
    """Await `run`, failing loudly if it takes longer than `HANG_S`: nothing may hang.

    Raises:
        AssertionError: `run` did not finish in time (it is cancelled first).
    """
    try:
        return await asyncio.wait_for(run, HANG_S)
    except TimeoutError as exc:
        raise AssertionError(f"chaos scenario {scenario!r} hung past {HANG_S}s") from exc


def open_leases(events: Iterable[PheromoneEvent]) -> set[str]:
    """Every lease id the trail shows leased and never released, Virtual Cells' own aside."""
    rows = list(events)
    virtual = {row.subject_id for row in rows if row.kind == "cell.provisioned"}
    leased = {
        str(row.payload["lease_id"])
        for row in rows
        if row.kind == _LEASED and "lease_id" in row.payload and row.subject_id not in virtual
    }
    released = {str(row.payload.get("lease_id")) for row in rows if row.kind == _RELEASED}
    return leased - released


def open_grants(events: Iterable[PheromoneEvent]) -> set[str]:
    """Every grant id the trail shows granted and never revoked or expired."""
    rows = list(events)
    granted = {row.subject_id for row in rows if row.kind == _GRANTED}
    ended = {row.subject_id for row in rows if row.kind in _GRANT_ENDS}
    return granted - ended


async def assert_nothing_left_open(hive: Hive) -> None:
    """Assert, once `run_hive` has returned, that no lease, grant or Virtual Cell outlived it.

    Raises:
        AssertionError: Something is still open, named in the message.
    """
    events = await hive.stores.trail.query(TrailQuery(limit=MAX_QUERY_LIMIT))
    # The Hive Stand's own lease is always taken: a trail with none would make the bar vacuous.
    assert any(event.kind == _LEASED for event in events), "the trail holds no lease at all"
    assert not open_leases(events), f"leases left open: {open_leases(events)}"
    assert not open_grants(events), f"grants left open: {open_grants(events)}"
    live = hive.queen_deps.ledger.live_grants()
    assert not live, f"the Forage ledger still holds {[str(grant.id) for grant in live]}"
    if hive.virtual_cells is not None:
        cells = await list_all_virtual(hive.virtual_cells.registry, hive.manifest.hive.id)
        assert not cells, f"Virtual Cells left on a backend: {[r.cell_id for _, r in cells]}"


async def assert_explicit_ends(hive: Hive) -> None:
    """Assert every task the Hive holds ended: succeeded, failed or cancelled, never left hanging.

    Raises:
        AssertionError: A task is in any other status, named in the message.
    """
    tasks = await hive.stores.chamber.list(TaskFilter())
    hanging = [
        (task.spec.title, task.status.value) for task in tasks if task.status not in _TERMINAL
    ]
    assert tasks and not hanging, f"tasks without an explicit end: {hanging}"
