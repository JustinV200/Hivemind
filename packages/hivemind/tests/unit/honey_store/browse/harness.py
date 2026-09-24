"""Test harness for hivemind.honey_store.browse: a real Honey Store beside fake wax and Bee Bread.

Every browse test runs over a real `SqliteHoneyStore` on a temp file (the Pheromone Trail's
migrations applied first, so the events a write records can be read back), with rows ripened the
way the Ripener stores them, and over the shipped in-memory `FakeLiveWaxSource` and
`FakeBeeBreadSource` for the two folders that live in memory. `BrowseHive` bundles one test's
store, trail, clock and human identity, and builds everything a test needs from them.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5) local to this test package, not shipped. Used
    by every test module under tests/unit/honey_store/browse/.

Key invariants:
    - None: this module holds test helpers only.

See Also:
    - hivemind.honey_store.browse for the package under test.
    - builders.honey for the Nectar and Honey draft builders reused here.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from builders.honey import make_honey_draft, make_nectar_draft

from hivemind.cell import HoneyClearance
from hivemind.common.sqlite import connect
from hivemind.guard import CapabilitySet
from hivemind.honey_store import HoneyIdentity, HoneyRetriever, RetrieverDeps, honey_event
from hivemind.honey_store.browse import (
    BeeBreadNote,
    BrowserDeps,
    BrowseSources,
    FakeBeeBreadSource,
    FakeLiveWaxSource,
    HoneyBrowser,
    WaxNote,
)
from hivemind.honey_store.honey import HoneyReader
from hivemind.honey_store.models import Honey, HoneyPart
from hivemind.honey_store.scope import queen_read_capabilities
from hivemind.honey_store.store import NectarAdded
from hivemind.honey_store.store.sqlite import SqliteHoneyStore
from hivemind.manifest import HoneyRetrievalSection
from hivemind.pheromone import HoneyEvent, SqlitePheromoneTrail, TrailQuery
from waggle.clock import FakeClock
from waggle.ids import CellId, new_event_id, new_hive_id, new_node_id, new_worker_id
from waggle.messages.cell.wax import WaxOrigin, WaxSeverity

__all__ = ["BrowseHive", "open_browse_hive", "reader"]

_EVENT_QUERY_LIMIT = 100  # Far more events than any one test records.


@dataclass(frozen=True, slots=True)
class BrowseHive:
    """One test's Honey Store, trail, clock and the human identity a note is recorded under."""

    store: SqliteHoneyStore
    trail: SqlitePheromoneTrail
    clock: FakeClock
    identity: HoneyIdentity

    async def ripen(
        self,
        body: str,
        *,
        scope: str = "hive",
        clearance: HoneyClearance = HoneyClearance.C1,
        chunks: tuple[str, ...] = (),
    ) -> tuple[Honey, ...]:
        """Deposit one Nectar filed under `scope` at `clearance`, and ripen it into Honey rows.

        Returns:
            The SUMMARY row, then one CHUNK row per entry of `chunks`.
        """
        draft = make_nectar_draft(
            clock=self.clock,
            content=f"{body} {new_event_id(self.clock)}".encode(),
            scope=scope,
            clearance=clearance,
        )

        def events(added: NectarAdded) -> tuple[HoneyEvent, ...]:
            return (
                honey_event(self.identity, self.clock, "honey.nectar_received", added.nectar.id),
            )

        added = await self.store.add_nectar(
            draft, hashlib.sha256(draft.content).hexdigest(), events
        )
        parts = [make_honey_draft(title=body[:40], summary=body, body=body, clearance=clearance)]
        parts += [
            make_honey_draft(
                part=HoneyPart.CHUNK,
                chunk_index=index,
                title=body[:40],
                body=chunk,
                clearance=clearance,
            )
            for index, chunk in enumerate(chunks, start=1)
        ]
        event = honey_event(self.identity, self.clock, "honey.ripened", added.nectar.id)
        return await self.store.ripen(added.nectar.id, parts, event)

    def deps(
        self,
        wax: Iterable[WaxNote] = (),
        bee_bread: Iterable[BeeBreadNote] = (),
        *,
        searchable: bool = False,
    ) -> BrowserDeps:
        """Build browser deps over this store and fake sources; a full-text retriever if asked."""
        sources = BrowseSources(wax=FakeLiveWaxSource(wax), bee_bread=FakeBeeBreadSource(bee_bread))
        return BrowserDeps(
            store=self.store,
            sources=sources,
            identity=self.identity,
            clock=self.clock,
            retriever=self.retriever() if searchable else None,
        )

    def browser(
        self,
        wax: Iterable[WaxNote] = (),
        bee_bread: Iterable[BeeBreadNote] = (),
        *,
        searchable: bool = False,
    ) -> HoneyBrowser:
        """Build a HoneyBrowser over `deps`."""
        return HoneyBrowser(self.deps(wax, bee_bread, searchable=searchable))

    def retriever(self) -> HoneyRetriever:
        """Build a full-text-only retriever over this store (no embedder bound)."""
        return HoneyRetriever(
            RetrieverDeps(self.store, self.identity, self.clock, HoneyRetrievalSection())
        )

    def wax_note(self, cell_id: CellId, **overrides: object) -> WaxNote:
        """Build one live wax note about `cell_id`: a C1 CAUTION, proposed now, standing."""
        fields: dict[str, object] = {
            "id": f"wax_{new_event_id(self.clock).removeprefix('event_')}",
            "cell_id": cell_id,
            "severity": WaxSeverity.CAUTION,
            "text": "The Wi-Fi on this Cell drops every hour.",
            "reason": "Seen on two runs.",
            "origin": WaxOrigin.BEE,
            "proposer": new_worker_id(self.clock),
            "clearance": HoneyClearance.C1,
            "task_id": None,
            "proposed_at": self.clock.now(),
            "expires_at": None,
        }
        fields.update(overrides)
        return WaxNote(**fields)

    def bee_bread_note(self, **overrides: object) -> BeeBreadNote:
        """Build one C1 TRANSCRIPT Bee Bread entry about no task, written now."""
        fields: dict[str, object] = {
            "id": new_event_id(self.clock),
            "kind": "TRANSCRIPT",
            "task_id": None,
            "clearance": HoneyClearance.C1,
            "created_at": self.clock.now(),
            "text": None,
            "payload": "make build\nok\n",
            "ref_ids": (),
        }
        fields.update(overrides)
        return BeeBreadNote(**fields)

    async def events(self, kind: str) -> list[HoneyEvent]:
        """Return every event of `kind` on the trail, oldest first."""
        found = await self.trail.query(TrailQuery(kind=kind, limit=_EVENT_QUERY_LIMIT))
        return [event for event in found if isinstance(event, HoneyEvent)]


async def open_browse_hive(tmp_path: Path) -> BrowseHive:
    """Open a fresh temp-file Honey Store with its trail, a FakeClock and a human identity."""
    clock = FakeClock()
    connection = connect(tmp_path / "hive.sqlite3")
    trail = await SqlitePheromoneTrail.create(connection, clock)
    store = await SqliteHoneyStore.create(connection, clock)
    identity = HoneyIdentity(hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor="human")
    return BrowseHive(store=store, trail=trail, clock=clock, identity=identity)


def reader(
    capabilities: CapabilitySet | None = None, ceiling: HoneyClearance = HoneyClearance.C2
) -> HoneyReader:
    """Build a reader: every scope up to C2 unless told otherwise."""
    return HoneyReader(
        requester=new_hive_id(FakeClock()),
        capabilities=capabilities if capabilities is not None else queen_read_capabilities(),
        ceiling=ceiling,
        is_night_veil=False,
    )
