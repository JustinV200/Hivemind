"""Tests for hivemind.workers.roles.drone.sources.DroneSources.handoff.

Fits into the Hive:
    Mirrors src/hivemind/workers/roles/drone/sources.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.roles.drone.sources for the module under test.
"""

from __future__ import annotations

from builders.memory import make_handoff
from builders.workers import make_assignment, make_context

from hivemind.workers.roles.drone.sources import DroneSources


async def test_handoff_returns_the_resumed_handoff_verbatim() -> None:
    ctx = make_context()
    assignment = make_assignment()
    handoff = make_handoff(do_not_redo=("Do not write scratch/output.txt again.",))
    sources = DroneSources(ctx, assignment, handoff)

    assert await sources.handoff() is handoff


async def test_handoff_returns_none_for_a_fresh_attempt() -> None:
    ctx = make_context()
    assignment = make_assignment()
    sources = DroneSources(ctx, assignment, None)

    assert await sources.handoff() is None
