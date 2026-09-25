"""Define the backend backoff: rest a Virtual backend whose provisions keep failing, for a while.

A Virtual backend that fails every provision (its daemon down, its image gone) used to be chosen
again on every dispatch pass that found a task waiting for it: each pass started one more round,
the provision and ADR-0028's retry, and each round wrote the lifecycle's `cell.provisioning` and
`cell.provision_failed`, thousands an hour at the default tick. Placement now remembers such a
backend (`QueenDeps.dispatch.backoff`, a `ProvisionBackoff`). After a round whose provision on it
failed (`note_failed`; the retry never tries the same backend twice, so each round fails a
backend once), placement skips it for `floor_s`, twice as long after each further round that
fails in a row, never longer than `cap_s`. Once a hold is over the backend is tried again, one
provision at a time: however many tasks wait on it, a backend still down costs one attempt a
round. A provision on it that succeeds ends the run (`note_served`). While a hold lasts,
`hivemind.queen.dispatcher.snapshot.current_virtual_backends` marks the backend (`mark_held`) and
`hivemind.queen.placement.decide` skips it with that one reason, so a task waiting on it is said
to wait once (`hivemind.queen.dispatcher.ready`), not once per round. The trail says so twice: one
`queen.decided` (reason `backend_held_back`) when a run begins, and one (`backend_restored`) when
a provision on the backend succeeds again; each round's own attempts stay the lifecycle's `cell.*`
rows, now a bounded few. Provisions already in flight when a round fails belong to that round,
so a backend several tasks were being given Cells on at once is not held back several times over.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the `queen.dispatcher`
    sub-package. Called by `hivemind.queen.dispatcher.acquire` (note_failed, note_served: each
    acquisition, beside the tick) and `hivemind.queen.dispatcher.snapshot` (mark_held, on every
    placement decision). Calls into `hivemind.hive` (CellProvisionError), `hivemind.pheromone`
    (MAX_PAYLOAD_STRING_CHARS), `hivemind.queen.deps` (BackendHold, QueenDeps),
    `hivemind.queen.placement` (Placement, ProvisionVirtual, VirtualBackendCandidate) and
    `hivemind.queen.trail` (record_event) only.

Key invariants:
    - Only a fresh provision's failure counts against its backend: a dormant Cell that fails to
      resume is that Cell's own failure (docs/adr/0029), and the retry already passes it over.
    - A backend's hold never exceeds `ProvisionBackoff.cap_s`, and a run's holds never shrink
      until a provision on the backend succeeds and ends it.
    - A run records exactly two `queen.decided` rows, subject the Hive: one when it begins, one
      when it ends; the rounds between record nothing of their own here.

See Also:
    - hivemind.queen.deps for ProvisionBackoff and BackendHold, the book this module keeps.
    - hivemind.queen.dispatcher.acquire for the acquisitions whose outcomes feed it.
    - docs/adr/0028-placement-policy-real-versus-virtual.md for the one retry a round includes.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime

from hivemind.hive import CellProvisionError
from hivemind.pheromone import MAX_PAYLOAD_STRING_CHARS
from hivemind.queen.deps import BackendHold, QueenDeps
from hivemind.queen.placement import Placement, ProvisionVirtual, VirtualBackendCandidate
from hivemind.queen.trail import record_event

# The reason placement names for a held backend. One text for the whole run (no count, no
# time), so a task waiting on the backend is said to wait once, however many rounds pass.
HELD_BACK = "held back after a failed provision"

__all__ = ["HELD_BACK", "mark_held", "note_failed", "note_served"]


def mark_held(
    deps: QueenDeps, backend: VirtualBackendCandidate, in_flight: int
) -> VirtualBackendCandidate:
    """Return `backend`, marked held back while a run of failed rounds rests it.

    Args:
        deps: The Queen's collaborators; `dispatch.backoff` is read.
        backend: One backend, as placement would otherwise see it.
        in_flight: The fresh Cells being provisioned on it right now.

    Returns:
        `backend` unchanged when no run holds it. Marked (`held_back`) while its latest hold
        lasts, and after that while a provision on it is in flight: that one is the run's next
        round, and no other starts beside it.
    """
    hold = deps.dispatch.backoff.holds.get(backend.name)
    if hold is None:
        return backend
    # Rested until the hold ends; then tried once at a time until a provision on it succeeds.
    if deps.clock.now() < hold.until or in_flight > 0:
        return dataclasses.replace(backend, held_back=HELD_BACK)
    return backend


async def note_failed(
    deps: QueenDeps, placement: Placement, failure: CellProvisionError, started: datetime
) -> None:
    """Hold back the backend a fresh provision just failed on, twice as long as last time.

    Args:
        deps: The Queen's collaborators; `dispatch.backoff` is written.
        placement: What the failed acquisition was for; only a `ProvisionVirtual` counts.
        failure: Why the provision failed, named on the row a new run records.
        started: When the failed attempt began.
    """
    if not isinstance(placement, ProvisionVirtual):
        return  # A dormant Cell's failed resume is its own, never its backend's (module docstring).
    book = deps.dispatch.backoff
    previous = book.holds.get(placement.backend)
    # Already in flight when this run's latest round failed: a part of that round, not a new one.
    if previous is not None and started <= previous.failed_at:
        return
    grown = book.floor_s if previous is None else previous.hold_s * 2
    hold = BackendHold(
        failures=1 if previous is None else previous.failures + 1,
        failed_at=deps.clock.now(),
        hold_s=min(book.cap_s, grown),
    )
    book.holds[placement.backend] = hold
    if previous is not None:
        return  # The run goes on: said when it began, and said again only when it ends.
    await record_event(
        deps,
        "queen.decided",
        deps.identity.hive_id,
        reason="backend_held_back",
        backend=placement.backend,
        image=placement.spec.image,
        hold_s=hold.hold_s,
        detail=failure.reason[:MAX_PAYLOAD_STRING_CHARS],
    )


async def note_served(deps: QueenDeps, placement: Placement) -> None:
    """End the run of failed rounds of the backend a fresh provision just succeeded on.

    Args:
        deps: The Queen's collaborators; `dispatch.backoff` is written.
        placement: What the acquisition was for; only a `ProvisionVirtual` names a backend.
    """
    if not isinstance(placement, ProvisionVirtual):
        return
    ended = deps.dispatch.backoff.holds.pop(placement.backend, None)
    if ended is None:
        return  # No run to end: the backend had not failed since it last served.
    await record_event(
        deps,
        "queen.decided",
        deps.identity.hive_id,
        reason="backend_restored",
        backend=placement.backend,
        failures=ended.failures,
    )
