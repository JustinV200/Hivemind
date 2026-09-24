"""Build valid hivemind.honey_store test data without repeating pydantic boilerplate per test.

Every builder here returns a real, validated model (codingrules 14.5) with sensible defaults for
every field a test does not care about. `open_test_honey_store` is the one async helper: a fresh
temp-file Hive with the Pheromone Trail's own migrations applied first (`SqliteHoneyStore.create`
requires `pheromone_events` to already exist), matching every store contract suite's own fixture
shape.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by
    packages/hivemind/tests/unit/honey_store/** and packages/hivemind/tests/contracts/
    test_honey_store_contract.py.

Key invariants:
    - Every builder that mints an id or a timestamp takes an optional `clock: Clock` (default a
      fresh FakeClock) so a test run is deterministic.
    - `make_nectar_draft`'s and `make_honey_draft`'s defaults pass their own model's validators
      with no further overrides needed.

See Also:
    - .claude/codingrules.md section 14.5 for the builders-over-fixtures rule this module follows.
    - hivemind.honey_store.models for NectarDraft, HoneyDraft, the shapes built here.
    - hivemind.honey_store.store.sqlite for SqliteHoneyStore, `open_test_honey_store`'s own return.
"""

from __future__ import annotations

from pathlib import Path

from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.common.sqlite import connect
from hivemind.honey_store.models import HoneyDraft, HoneyPart, NectarDraft, NectarOrigin
from hivemind.honey_store.store.sqlite import SqliteHoneyStore
from hivemind.pheromone import SqlitePheromoneTrail
from waggle.clock import Clock, FakeClock
from waggle.ids import new_cell_id, new_task_id, new_worker_id
from waggle.messages.honey import NectarKind

__all__ = ["make_honey_draft", "make_nectar_draft", "open_test_honey_store"]


def make_nectar_draft(clock: Clock | None = None, **overrides: object) -> NectarDraft:
    """Build a valid, C1, BEE-origin NectarDraft with a fresh task, Cell and Worker.

    Args:
        clock: Source of the default task/Cell/Worker ids and timestamp; a fresh FakeClock when
            omitted.
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated NectarDraft. Note for a test that calls this more than once and stores each
        result through `HoneyStore.add_nectar`: the default `content` is fixed, and `add_nectar`
        dedupes by content sha256, so two unmodified drafts land as one row; override `content`
        (or `source_key`) when the test needs genuinely distinct rows.
    """
    active_clock = clock if clock is not None else FakeClock()
    fields: dict[str, object] = {
        "kind": NectarKind.FINDING,
        "origin": NectarOrigin.BEE,
        "media_type": "text/plain",
        "title": "A test finding",
        "content": b"The widget factory's staging config lives at /etc/widgets/staging.toml.",
        "task_id": new_task_id(active_clock),
        "cell_id": new_cell_id(active_clock),
        "bee": new_worker_id(active_clock),
        "observed_at": active_clock.now(),
        "clearance": HoneyClearance.C1,
        "origin_tier": CombShieldLevel.MEADOW,
        "scope": "hive",
    }
    fields.update(overrides)
    return NectarDraft(**fields)


def make_honey_draft(**overrides: object) -> HoneyDraft:
    """Build a valid, C1 SUMMARY HoneyDraft.

    Args:
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated HoneyDraft.
    """
    fields: dict[str, object] = {
        "part": HoneyPart.SUMMARY,
        "chunk_index": 0,
        "title": "A test finding",
        "summary": "The staging config lives at /etc/widgets/staging.toml.",
        "body": "The staging config lives at /etc/widgets/staging.toml.",
        "clearance": HoneyClearance.C1,
    }
    fields.update(overrides)
    return HoneyDraft(**fields)


async def open_test_honey_store(
    tmp_path: Path, clock: Clock, *, python_vectors: bool = False
) -> SqliteHoneyStore:
    """Build a SqliteHoneyStore on a fresh temp-file Hive, the trail's migrations applied first.

    Args:
        tmp_path: A writable directory (normally pytest's own `tmp_path` fixture) to place the
            database file in.
        clock: Injected clock, shared with the Pheromone Trail and the store.
        python_vectors: Force the Python cosine fallback instead of sqlite-vec.

    Returns:
        A ready SqliteHoneyStore.
    """
    connection = connect(tmp_path / "hive.sqlite3")
    # SqliteHoneyStore.create refuses without pheromone_events already on the connection.
    await SqlitePheromoneTrail.create(connection, clock)
    return await SqliteHoneyStore.create(connection, clock, force_python_vectors=python_vectors)
