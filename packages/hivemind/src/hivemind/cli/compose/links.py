"""Build the one Queen<->Warden Waggle link every Hive this phase composes needs: build_hive_links.

Phase 3 runs every bee in one process (the phase 3 design's own "process layout" decision, recorded
here as codingrules section 4's layering and ADR-0012/ADR-0019, never as a citation to a session
brief): the Queen (the Hive's central orchestrator) and the Hive Stand's one Warden (the per-Cell
supervisor for the machine the Queen runs on) talk over an unsigned
`waggle.transport.memory.MemoryTransport` pair instead of a WebSocket, exactly the shape
`tests.builders.queen.make_queen_deps`'s own `_build_link` already builds for tests. `build_hive_
links` is that same construction, shipped: it mints the Warden's own id, builds the pair, and
addresses each end's `waggle.envelope.Hop` as `hive_<id>` (the Queen) and `warden_<id>` (the
Warden), both carrying the manifest's own `[hive] node_id`.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.compose`. Called by
    `hivemind.cli.compose.hive.build_hive`, once per Hive. Calls into `hivemind.cell` (Cell),
    `hivemind.queen.deps` (WardenLink) and waggle only.

Key invariants:
    - `HiveLinks.queen_link.hop.sender` and `HiveLinks.warden_hop.sender` are each other's
      `recipient`, and both carry the same `node_id` (one process, one node this phase).
    - The `MemoryTransport.pair` this builds is unsigned (`waggle.codec.Codec()` with no
      Verifier): phase 3's own process layout keeps every bee's link in one process, so there is
      no wire to forge.

See Also:
    - .claude/codingrules.md section 4 for the layer 7 row this package occupies.
    - hivemind.queen.deps for WardenLink, the Queen-side half this module builds.
    - hivemind.wardens.deps for WardenDeps.queen_link/.hop, the Warden-side half this module's
      other two return values feed.
    - tests.builders.queen for _build_link, the test-only construction this module now ships.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.cell import Cell
from hivemind.queen.deps import WardenLink
from waggle.clock import Clock
from waggle.codec import Codec
from waggle.envelope import Hop
from waggle.ids import HiveId, NodeId, WardenId, new_warden_id
from waggle.transport.base import Transport
from waggle.transport.memory import MemoryTransport

__all__ = ["HiveLinks", "build_hive_links"]


@dataclass(frozen=True, slots=True)
class HiveLinks:
    """Both ends of the one Queen<->Warden link this phase's Hive needs.

    Attributes:
        warden_id: The freshly minted id for the Hive Stand's one Warden.
        queen_link: The Queen's own end, ready for `hivemind.queen.queen.Queen.attach_warden`.
        warden_transport: The Warden's own end of the same pair, for `WardenDeps.queen_link`.
        warden_hop: The Warden's own address, for `WardenDeps.hop`.
    """

    warden_id: WardenId
    queen_link: WardenLink
    warden_transport: Transport
    warden_hop: Hop


def build_hive_links(hive_id: HiveId, node_id: NodeId, cell: Cell, clock: Clock) -> HiveLinks:
    """Build the Queen<->Warden link: an unsigned, in-process MemoryTransport pair.

    Args:
        hive_id: The Hive's own id (`[hive] id`); addresses the Queen's own end as `hive_<id>`.
        node_id: This process's own node id (`[hive] node_id`); stamped on both ends' hops.
        cell: The Hive Stand's own Cell, from `hivemind.cell.local.HiveStandSource.cells`; the
            Queen-side `WardenLink` carries it so placement can read its capabilities and capacity
            without ever holding the Warden's own session.
        clock: Source of the freshly minted `WardenId`.

    Returns:
        Both ends of the link, plus the minted `WardenId` both hops address.
    """
    warden_id = new_warden_id(clock)
    queen_transport, warden_transport = MemoryTransport.pair(Codec(), Codec())
    queen_hop = Hop(sender=hive_id, recipient=warden_id, node_id=node_id)
    warden_hop = Hop(sender=warden_id, recipient=hive_id, node_id=node_id)
    queen_link = WardenLink(
        warden_id=warden_id, cell=cell, transport=queen_transport, hop=queen_hop
    )
    return HiveLinks(
        warden_id=warden_id,
        queen_link=queen_link,
        warden_transport=warden_transport,
        warden_hop=warden_hop,
    )
