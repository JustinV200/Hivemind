"""Tests for hivemind.honey_store.ripening.prune: drop a superseded model's vectors on request.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/ripening/prune.py (codingrules section 3). Runs over a real
    SQLite Honey Store; the store's own coverage-check-then-delete transaction is covered by
    tests/contracts/test_honey_store_contract.py, so these tests focus on prune_vectors' own
    event-building and PruneOutcome mapping.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.ripening.prune for the module under test.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from builders.honey import make_honey_draft, make_nectar_draft, make_ripener_deps
from builders.honey import open_test_honey_store_with_trail as open_store_with_trail

from hivemind.honey_store.identity import honey_event
from hivemind.honey_store.ripening.deps import RipenerDeps
from hivemind.honey_store.ripening.prune import VECTORS_PRUNED_KIND, prune_vectors
from hivemind.pheromone import TrailQuery
from waggle.clock import FakeClock
from waggle.ids import HoneyId


async def _ripen_row(deps: RipenerDeps, body: str) -> HoneyId:
    """Deposit and ripen one distinct one-row Nectar; return the new Honey row's own id."""
    draft = make_nectar_draft(clock=deps.clock, content=body.encode())
    digest = hashlib.sha256(draft.content).hexdigest()
    added = await deps.store.add_nectar(draft, digest, lambda _added: ())
    event = honey_event(deps.identity, deps.clock, "honey.ripened", added.nectar.id)
    rows = await deps.store.ripen(added.nectar.id, (make_honey_draft(body=body),), event)
    return rows[0].id


async def test_prune_vectors_refuses_and_records_nothing_while_a_row_lacks_the_kept_model(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    store, trail = await open_store_with_trail(tmp_path, clock)
    deps = make_ripener_deps(store, clock)
    covered = await _ripen_row(deps, "covered row")
    await _ripen_row(deps, "uncovered row")  # Never given a "kept" vector.
    await store.set_vectors([(covered, (1.0, 0.0))], "kept", None)

    outcome = await prune_vectors(deps, "kept")

    assert outcome.refused
    assert outcome.missing == 1
    assert outcome.dropped == {}
    assert await trail.query(TrailQuery(kind=VECTORS_PRUNED_KIND)) == ()


async def test_prune_vectors_drops_every_other_model_and_keeps_the_kept_ones(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    store, trail = await open_store_with_trail(tmp_path, clock)
    deps = make_ripener_deps(store, clock)
    first = await _ripen_row(deps, "first row")
    second = await _ripen_row(deps, "second row")
    await store.set_vectors([(first, (1.0, 0.0)), (second, (0.0, 1.0))], "kept", None)
    await store.set_vectors([(first, (1.0, 0.0))], "old-a", None)
    await store.set_vectors([(first, (1.0, 0.0)), (second, (0.0, 1.0))], "old-b", None)

    outcome = await prune_vectors(deps, "kept")

    assert not outcome.refused
    assert outcome.missing == 0
    assert outcome.dropped == {"old-a": 1, "old-b": 2}
    stats = await store.stats()
    assert stats.vectors_by_model == {"kept": 2}  # Only the kept model's vectors survive.
    (event,) = await trail.query(TrailQuery(kind=VECTORS_PRUNED_KIND))
    assert event.payload == {"kept_model": "kept", "dropped_models": 2, "dropped_rows": 3}


async def test_prune_vectors_reports_nothing_dropped_when_no_other_model_ever_existed(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    store, trail = await open_store_with_trail(tmp_path, clock)
    deps = make_ripener_deps(store, clock)
    only = await _ripen_row(deps, "only row")
    await store.set_vectors([(only, (1.0, 0.0))], "kept", None)

    outcome = await prune_vectors(deps, "kept")

    assert not outcome.refused
    assert outcome.dropped == {}
    (event,) = await trail.query(TrailQuery(kind=VECTORS_PRUNED_KIND))
    assert event.payload == {"kept_model": "kept", "dropped_models": 0, "dropped_rows": 0}
