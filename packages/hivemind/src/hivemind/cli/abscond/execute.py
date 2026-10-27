"""Carry out an Absconding the human confirmed, and record it on the trail.

Roadmap step 13.4, once `hivemind.cli.abscond.consent` passed and the human confirmed the scope
review: `run_abscond` (5.13's pass) destroys every Virtual Cell, dormant ones included, revokes
their grants, and releases or defers every lease (a running Queen's and a device's go out as
RELEASE orders, whose release by their Warden kills the Workers' processes and removes their
scratch); then every temp dir is removed, every resident Basket row evicted, and every Leaving the
human ticked replayed away. Persistent stores are not wiped: the trail, the Brood Chamber, Honey
and the ledger stay. `guard.absconded` is recorded last, as the human, with the reason, the
confirmation evidence (how the human proved themselves, never the password or the phrase), what
the scope held and what each step did. It is recorded even when a step fails partway (Docker down,
a locked Leaving): then it holds what was done before the failure and the failure's type, and the
failure is raised on to the caller.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.abscond`. Called by
    `.command`. Calls into `hivemind.basket.store`, `hivemind.cell` (CellIdentity),
    `hivemind.cell.leavings`, `hivemind.cell.local` (remove_scratch),
    `hivemind.cli.readback.leavings` (remove_leaving), `hivemind.cli.readback.virtual_abscond`,
    `hivemind.guard.policy` (OPERATOR_ID), `hivemind.pheromone`, the package's own scope and
    consent, and waggle only.

Key invariants:
    - Only Leavings the human ticked are removed; every other one stays where it was left.
    - `guard.absconded` is recorded once per Absconding, after every effect or at the failure that
      stopped them, carrying ids, counts and an exception type only.

See Also:
    - hivemind.cli.readback.virtual_abscond for the Cells-and-leases pass this builds on.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from pydantic import JsonValue

from hivemind.basket.store import BasketStore
from hivemind.cell import CellIdentity
from hivemind.cell.leavings import Leaving
from hivemind.cell.local import remove_scratch
from hivemind.cli.abscond.consent import Evidence
from hivemind.cli.abscond.scope import AbscondScope
from hivemind.cli.readback.leavings import remove_leaving
from hivemind.cli.readback.virtual_abscond import AbscondDeps, AbscondSummary, run_abscond
from hivemind.guard.policy import OPERATOR_ID
from hivemind.pheromone import GuardEvent
from waggle.ids import EventId, new_event_id

ABSCONDED_KIND = "guard.absconded"  # The human's break-glass teardown, recorded last.

__all__ = ["ABSCONDED_KIND", "AbscondReceipt", "carry_out"]


@dataclass(frozen=True, slots=True)
class AbscondReceipt:
    """What one Absconding did.

    Attributes:
        summary: The Cells-and-leases pass's own summary.
        temp_dirs_removed: Temp dirs removed.
        resident_evicted: Resident Basket rows evicted.
        leavings_removed: Ticked Leavings replayed away.
        event_id: The `guard.absconded` event.
    """

    summary: AbscondSummary
    temp_dirs_removed: int
    resident_evicted: int
    leavings_removed: int
    event_id: EventId


async def carry_out(
    deps: AbscondDeps,
    basket: BasketStore,
    scope: AbscondScope,
    ticked: tuple[Leaving, ...],
    evidence: Evidence,
) -> AbscondReceipt:
    """Tear down everything in `scope`, remove the ticked Leavings, and record it.

    Args:
        deps: The Cells-and-leases pass's collaborators (trail, ledger, orders, Leavings).
        basket: The Basket store whose resident rows are evicted.
        scope: What the human reviewed.
        ticked: The Leavings the human ticked for removal.
        evidence: The human's reason and how they confirmed.

    Returns:
        What it did.

    Raises:
        Exception: Whatever stopped a step, after `guard.absconded` recorded what came before it.
    """
    human = _human(deps)
    done = _Progress(event_id=new_event_id(deps.clock))
    try:
        summary = await _tear_down(deps, basket, scope, ticked, done)
    except BaseException as exc:
        done.failure = type(exc).__name__  # The type only: a message may quote a path or a host.
        raise
    finally:
        # The consent is on the trail whatever happened: who asked, why, and what was done.
        await deps.trail.record(_event(deps, human, scope, done, evidence))
    return AbscondReceipt(
        summary=summary,
        temp_dirs_removed=done.temp_dirs_removed,
        resident_evicted=done.resident_evicted,
        leavings_removed=done.leavings_removed,
        event_id=done.event_id,
    )


def _human(deps: AbscondDeps) -> CellIdentity:
    """The operator, as this Hive Stand stamps the human on the trail."""
    return CellIdentity(
        hive_id=deps.manifest.hive.id, node_id=deps.manifest.hive.node_id, actor=OPERATOR_ID
    )


@dataclass(slots=True)
class _Progress:
    """What an Absconding has done so far, filled in step by step for `guard.absconded`."""

    event_id: EventId
    summary: AbscondSummary | None = None
    temp_dirs_removed: int = 0
    resident_evicted: int = 0
    leavings_removed: int = 0
    failure: str | None = None


async def _tear_down(
    deps: AbscondDeps,
    basket: BasketStore,
    scope: AbscondScope,
    ticked: tuple[Leaving, ...],
    done: _Progress,
) -> AbscondSummary:
    """Run every step in order, counting each into `done` as it lands; return the pass's summary."""
    summary = done.summary = await run_abscond(deps)
    for path in scope.temp_dirs:
        # Blocking rmtree, off the loop; a lingering handle leaves one behind, counted, not raised.
        done.temp_dirs_removed += await asyncio.to_thread(remove_scratch, path)
    for cell_id, sha256 in scope.resident:
        await basket.evict(cell_id, sha256)
        done.resident_evicted += 1
    for leaving in ticked:
        await remove_leaving(deps.leavings, leaving, _human(deps), deps.clock)
        done.leavings_removed += 1
    return summary


def _event(
    deps: AbscondDeps,
    human: CellIdentity,
    scope: AbscondScope,
    done: _Progress,
    evidence: Evidence,
) -> GuardEvent:
    """Build `guard.absconded`: the human, the reason, the confirmation, the scope, the results."""
    payload: dict[str, JsonValue] = {
        "reason": evidence.reason,
        "confirmation": evidence.payload(),
        "scope": {
            "virtual_cells": len(scope.cells),
            "dormant_cells": scope.dormant,
            "stand_leases": len(scope.stand_leases),
            "device_leases": len(scope.device_leases),
            "grants": len(scope.grant_ids),
            "workers": len(scope.workers),
            "temp_dirs": len(scope.temp_dirs),
            "resident_blobs": len(scope.resident),
            "leavings": len(scope.leavings),
        },
        **_pass_counts(done.summary),
        "temp_dirs_removed": done.temp_dirs_removed,
        "resident_evicted": done.resident_evicted,
        "leavings_removed": done.leavings_removed,
        "failed": done.failure,
    }
    return GuardEvent(
        id=done.event_id,
        hive_id=human.hive_id,
        node_id=human.node_id,
        at=deps.clock.now(),
        actor=human.actor,
        kind=ABSCONDED_KIND,
        subject_id=human.hive_id,
        payload=payload,
    )


def _pass_counts(summary: AbscondSummary | None) -> dict[str, JsonValue]:
    """The Cells-and-leases pass's counts; each None when that pass itself failed."""
    if summary is None:
        keys = ("containers_destroyed", "leases_released", "leases_deferred", "leases_left")
        return dict.fromkeys((*keys, "grants_revoked", "left_as_found"))
    return {
        "containers_destroyed": summary.containers_destroyed,
        "leases_released": summary.leases_released,
        "leases_deferred": summary.leases_deferred_to_queen,
        "leases_left": summary.leases_left_untouched,
        "grants_revoked": summary.grants_revoked,
        "left_as_found": summary.left_as_found,
    }
