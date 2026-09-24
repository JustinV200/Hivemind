"""Provide the Harness shape and small builders `test_honey_store_contract` shares across clauses.

`test_honey_store_contract.py` and `test_honey_store_lowering_contract.py` write each contract
clause once against `hivemind.honey_store.store.protocol.HoneyStore` and run it over three
harnesses of the one shipped implementation (`hivemind.honey_store.store.sqlite.
SqliteHoneyStore`): a temp file with sqlite-vec, a temp file with the Python vector fallback forced
on, and `:memory:` with sqlite-vec (ADR-0031); each module's own `@pytest.fixture` builds one
(`open_harness`), parametrised over `HARNESS_KINDS`, mirroring `test_llm_provider_contract.py`'s
own local fixture over harnesses this module's sibling `llm_provider_harness.py` supplies. This
module holds only what has no fixture machinery of its own: the `Harness` shape every clause
takes, the one way to open a harness of each kind, and the builders (`event`, `nectar_events`,
`prune_events`, `sha`, `ripen`) that turn the store's own API into one-line steps -- kept here,
mirroring `llm_provider_harness.py`/`embedding_provider_harness.py`, so each contract file stays
under codingrules 5.1's 400-line test-file limit as the suite grows (ADR-0033 added the sources,
scope and prune clauses; ADR-0034 the lowering clauses, in their own module).

Fits into the Hive:
    Test infrastructure (codingrules section 14.3), not shipped. Used only by
    `contracts.test_honey_store_contract` and `contracts.test_honey_store_lowering_contract`.

Key invariants:
    - Every helper here builds well-formed values through the same public builders and store API
      a real caller would use; none reaches into private store internals.

See Also:
    - hivemind.honey_store.store.protocol for the HoneyStore contract under test.
    - packages/hivemind/tests/contracts/test_memory_store_contract.py for the pattern this mirrors.
    - packages/hivemind/tests/contracts/llm_provider_harness.py for the sibling this file mirrors.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from builders.honey import make_honey_draft, make_nectar_draft

from hivemind.cell import HoneyClearance
from hivemind.common.sqlite import connect
from hivemind.honey_store.models import Honey, NectarDraft
from hivemind.honey_store.scope import HIVE_SCOPE
from hivemind.honey_store.store import NectarAdded, NectarEvents, PruneEvents, PruneResult
from hivemind.honey_store.store.sqlite import SqliteHoneyStore
from hivemind.pheromone import HoneyEvent, PheromoneTrail, SqlitePheromoneTrail
from waggle.clock import FakeClock
from waggle.ids import new_event_id, new_hive_id, new_nectar_id, new_node_id

# The three harnesses every clause runs over: both storage modes and both vector backends.
HARNESS_KINDS = ("tempfile_sqlite_vec", "tempfile_python", "memory_sqlite_vec")

__all__ = [
    "HARNESS_KINDS",
    "Harness",
    "event",
    "nectar_events",
    "open_harness",
    "prune_events",
    "ripen",
    "sha",
]


@dataclass(frozen=True, slots=True)
class Harness:
    """One SqliteHoneyStore harness: its store, trail, raw connection and shared clock."""

    store: SqliteHoneyStore
    trail: PheromoneTrail
    connection: sqlite3.Connection
    clock: FakeClock


async def open_harness(kind: str, tmp_path: Path) -> Harness:
    """Open one harness of `kind` (one of `HARNESS_KINDS`): a fresh store over a trailed file."""
    clock = FakeClock()
    is_memory = kind == "memory_sqlite_vec"
    connection = connect(":memory:" if is_memory else tmp_path / "hive.sqlite3")
    trail = await SqlitePheromoneTrail.create(connection, clock)
    force_python = kind == "tempfile_python"
    store = await SqliteHoneyStore.create(connection, clock, force_python_vectors=force_python)
    return Harness(store=store, trail=trail, connection=connection, clock=clock)


def event(clock: FakeClock, kind: str, subject_id: str) -> HoneyEvent:
    """Build a well-formed HoneyEvent whose subject is `subject_id`, minting a fresh id/node."""
    return HoneyEvent(
        id=new_event_id(clock),
        hive_id=new_hive_id(clock),
        node_id=new_node_id(clock),
        at=clock.now(),
        actor="system",
        kind=kind,
        subject_id=subject_id,
        payload={},
    )


def nectar_events(clock: FakeClock) -> NectarEvents:
    """Build `add_nectar`'s events from its outcome: received for a new row, else deduplicated."""

    def build(added: NectarAdded) -> tuple[HoneyEvent, ...]:
        kind = "honey.nectar_received" if added.is_new else "honey.nectar_deduplicated"
        return (event(clock, kind, added.nectar.id),)

    return build


def prune_events(clock: FakeClock, subject_id: str) -> PruneEvents:
    """Build `prune_vectors`'s events from its outcome: nothing when refused, else one event."""

    def build(result: PruneResult) -> tuple[HoneyEvent, ...]:
        if result.missing > 0:
            return ()
        return (event(clock, "honey.vectors_pruned", subject_id),)

    return build


def sha(draft: NectarDraft) -> str:
    """Digest a NectarDraft's own content, the value `add_nectar`'s caller always supplies."""
    return hashlib.sha256(draft.content).hexdigest()


async def ripen(
    harness_: Harness,
    *,
    scope: str = HIVE_SCOPE,
    clearance: HoneyClearance = HoneyClearance.C1,
    body: str = "a widget finding",
) -> Honey:
    """Deposit one fresh Nectar and ripen it into a single SUMMARY Honey row; return that row.

    `add_nectar` dedupes by content sha256, and several tests deliberately ripen many rows that
    share the same `body` text (e.g. 20 "forbidden" rows); a fresh id salts the content each call
    so those still land as genuinely distinct Nectar rows instead of deduping onto one another.
    """
    salt = new_nectar_id(harness_.clock)
    draft = make_nectar_draft(
        clock=harness_.clock,
        scope=scope,
        clearance=clearance,
        task_id=None,
        bee=None,
        content=f"{body} ({salt})".encode(),
    )
    added = await harness_.store.add_nectar(draft, sha(draft), nectar_events(harness_.clock))
    rows = await harness_.store.ripen(
        added.nectar.id,
        (make_honey_draft(body=body, summary=body, clearance=clearance),),
        event(harness_.clock, "honey.ripened", added.nectar.id),
    )
    return rows[0]
