"""Build valid hivemind.honey_store test data without repeating pydantic boilerplate per test.

Every builder here returns a real, validated model (codingrules 14.5) with sensible defaults for
every field a test does not care about. `open_test_honey_store` is the one async helper: a fresh
temp-file Hive with the Pheromone Trail's own migrations applied first (`SqliteHoneyStore.create`
requires `pheromone_events` to already exist), matching every store contract suite's own fixture
shape; `open_test_honey_store_with_trail` is the same Hive plus the trail itself, for a test that
reads back the events a write recorded. The intake and ripening builders (`make_honey_identity`,
`make_nectar`, `make_nectar_submission`, `make_nectar_deposit`, `make_deposit_chunks`,
`make_ripener_deps`) build the inputs `hivemind.honey_store.nectar` and
`hivemind.honey_store.ripening` take.

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

import dataclasses
import hashlib
from pathlib import Path

from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.common.sqlite import connect
from hivemind.honey_store.identity import HoneyIdentity
from hivemind.honey_store.models import (
    HoneyDraft,
    HoneyPart,
    Nectar,
    NectarDraft,
    NectarOrigin,
    NectarState,
)
from hivemind.honey_store.nectar.submission import NectarSubmission
from hivemind.honey_store.ripening.deps import RipenerDeps
from hivemind.honey_store.store import HoneyStore
from hivemind.honey_store.store.sqlite import SqliteHoneyStore
from hivemind.manifest import HoneyRipeningSection
from hivemind.pheromone import SqlitePheromoneTrail
from waggle.clock import Clock, FakeClock
from waggle.ids import (
    new_cell_id,
    new_hive_id,
    new_nectar_id,
    new_node_id,
    new_task_id,
    new_worker_id,
)
from waggle.messages import CombShieldLevel as WireCombShieldLevel
from waggle.messages import HoneyClearance as WireHoneyClearance
from waggle.messages.honey import NectarDeposit, NectarKind

__all__ = [
    "make_deposit_chunks",
    "make_honey_draft",
    "make_honey_identity",
    "make_nectar",
    "make_nectar_deposit",
    "make_nectar_draft",
    "make_nectar_submission",
    "make_ripener_deps",
    "open_test_honey_store",
    "open_test_honey_store_with_trail",
]


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


async def open_test_honey_store_with_trail(
    tmp_path: Path, clock: Clock, *, python_vectors: bool = False
) -> tuple[SqliteHoneyStore, SqlitePheromoneTrail]:
    """Build `open_test_honey_store`'s Hive, returning the Pheromone Trail beside the store.

    Args:
        tmp_path: A writable directory to place the database file in.
        clock: Injected clock, shared with the Pheromone Trail and the store.
        python_vectors: Force the Python cosine fallback instead of sqlite-vec.

    Returns:
        `(store, trail)` on one connection, so the trail sees every event the store commits.
    """
    connection = connect(tmp_path / "hive.sqlite3")
    trail = await SqlitePheromoneTrail.create(connection, clock)
    store = await SqliteHoneyStore.create(connection, clock, force_python_vectors=python_vectors)
    return store, trail


def make_honey_identity(clock: Clock | None = None) -> HoneyIdentity:
    """Build a HoneyIdentity for a fresh Hive and node, acting as "system".

    Args:
        clock: Source of the minted ids; a fresh FakeClock when omitted.

    Returns:
        A HoneyIdentity every honey.* event a test mints can be stamped with.
    """
    active_clock = clock if clock is not None else FakeClock()
    return HoneyIdentity(
        hive_id=new_hive_id(active_clock), node_id=new_node_id(active_clock), actor="system"
    )


def make_nectar_submission(clock: Clock | None = None, **overrides: object) -> NectarSubmission:
    """Build a valid BEE-origin FINDING submission from a MEADOW Virtual Cell, declared C1.

    Args:
        clock: Source of the default task/Cell/Worker ids and timestamp; a fresh FakeClock when
            omitted.
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated NectarSubmission (fixed default content: override it for distinct rows).
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
        "declared": HoneyClearance.C1,
        "from_borrowed_cell": False,
        "tier": CombShieldLevel.MEADOW,
    }
    fields.update(overrides)
    return NectarSubmission(**fields)


def make_nectar_deposit(clock: Clock | None = None, **overrides: object) -> NectarDeposit:
    """Build one valid, single-chunk (offset 0, final) NectarDeposit whose digest matches its bytes.

    Args:
        clock: Source of the default task/Cell/Worker ids and timestamp; a fresh FakeClock when
            omitted.
        **overrides: Field values that replace the defaults below; `sha256` and `total_bytes`
            default to the (possibly overridden) `chunk`'s own.

    Returns:
        A validated NectarDeposit.
    """
    active_clock = clock if clock is not None else FakeClock()
    chunk = overrides.get("chunk", b"A deposited finding about the widget factory.")
    assert isinstance(chunk, bytes)
    fields: dict[str, object] = {
        "sha256": hashlib.sha256(chunk).hexdigest(),
        "kind": NectarKind.FINDING,
        "media_type": "text/plain",
        "title": "A deposited finding",
        "task_id": new_task_id(active_clock),
        "cell_id": new_cell_id(active_clock),
        "worker_id": new_worker_id(active_clock),
        "observed_at": active_clock.now(),
        "clearance": WireHoneyClearance.C1,
        "origin_tier": WireCombShieldLevel.MEADOW,
        "event_id": None,
        "chunk": chunk,
        "offset": 0,
        "total_bytes": len(chunk),
        "final": True,
    }
    fields.update(overrides)
    return NectarDeposit(**fields)


def make_deposit_chunks(
    content: bytes, chunk_bytes: int, template: NectarDeposit
) -> list[NectarDeposit]:
    """Split `content` into consecutive chunks that share `template`'s metadata.

    Args:
        content: The whole deposit's bytes.
        chunk_bytes: The largest chunk; must be at least 1.
        template: Supplies every whole-deposit field (task, Cell, Worker, label, ...).

    Returns:
        The chunks in offset order, the last one `final`, all carrying `content`'s digest and
        length, so feeding them to intake in order reassembles `content` exactly.
    """
    digest = hashlib.sha256(content).hexdigest()
    offsets = range(0, len(content), chunk_bytes)
    return [
        template.model_copy(
            update={
                "sha256": digest,
                "chunk": content[offset : offset + chunk_bytes],
                "offset": offset,
                "total_bytes": len(content),
                "final": offset + chunk_bytes >= len(content),
            }
        )
        for offset in offsets
    ]


def make_ripener_deps(store: HoneyStore, clock: Clock, **overrides: object) -> RipenerDeps:
    """Build RipenerDeps on `store` with default `[honey.ripening]` settings and no model at all.

    Args:
        store: The Honey Store a pass reads and writes.
        clock: Injected clock for every event the pass mints.
        **overrides: RipenerDeps fields that replace the defaults (`ripener`, `embedder`,
            `ripening`, gates, ...).

    Returns:
        A RipenerDeps with a fresh "system" identity, heuristic summaries and no vectors unless
        `overrides` binds a ripener or an embedder.
    """
    base = RipenerDeps(
        store=store,
        identity=make_honey_identity(clock),
        clock=clock,
        ripening=HoneyRipeningSection(),
    )
    return dataclasses.replace(base, **overrides)  # type: ignore[arg-type]


def make_nectar(clock: Clock | None = None, **overrides: object) -> Nectar:
    """Build a valid, stored-shaped RECEIVED Nectar row (C1, hive scope, MEADOW) without a store.

    Args:
        clock: Source of the default ids and timestamps; a fresh FakeClock when omitted.
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated Nectar, for a ripening stage that only reads a row's metadata.
    """
    active_clock = clock if clock is not None else FakeClock()
    fields: dict[str, object] = {
        "id": new_nectar_id(active_clock),
        "sha256": hashlib.sha256(b"a stored finding").hexdigest(),
        "size_bytes": 16,
        "kind": NectarKind.FINDING,
        "origin": NectarOrigin.BEE,
        "media_type": "text/plain",
        "title": "A stored finding",
        "task_id": new_task_id(active_clock),
        "cell_id": new_cell_id(active_clock),
        "bee": new_worker_id(active_clock),
        "observed_at": active_clock.now(),
        "received_at": active_clock.now(),
        "clearance": HoneyClearance.C1,
        "origin_tier": CombShieldLevel.MEADOW,
        "scope": "hive",
        "state": NectarState.RECEIVED,
        "ripen_attempts": 0,
        "tainted": False,
    }
    fields.update(overrides)
    return Nectar(**fields)
