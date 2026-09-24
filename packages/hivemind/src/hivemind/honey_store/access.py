"""Bundle one Hive's Honey Store handles: the store, intake, retriever, Ripener and their policy.

The Honey Store is the Hive's cold tier of memory: Nectar (raw deposits) comes in through
`NectarIntake`, the `Ripener` turns it into Honey (labelled, searchable knowledge), and the
`HoneyRetriever` answers queries under a reader's scope, clearance and budget. Every consumer
outside this package needs the same handful of them together -- the Queen takes deposits in,
answers queries and consults Honey before dispatch; the House Bee ripens; `hive honey` browses and
maintains -- so the composition root builds them once, over one store and one event identity, and
hands out this one frozen bundle instead of five loose collaborators (codingrules 5.1).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package. Built by
    the composition root (`hivemind.cli.compose.honey.build_honey_access`) and read by the Queen
    (`QueenDeps.honey`), the House Bee and the `hive honey` CLI. Calls into this package's own
    store, intake, ripening, retrieval and identity modules and `hivemind.manifest` (the three
    `[honey]` sections a consumer needs beside them) only.

Key invariants:
    - Every handle here shares one store and one identity: whatever a consumer writes through
      intake or the Ripener lands on the same file, stamped with the same Hive, node and actor.
    - Holds no state of its own; every field is a collaborator or an immutable manifest section.

See Also:
    - docs/adr/0031-honey-store-sqlite-fts5-sqlite-vec.md for who writes and how readers are
      filtered.
    - hivemind.cli.compose.honey for the one place a HoneyAccess is built.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.honey_store.honey import HoneyRetriever
from hivemind.honey_store.identity import HoneyIdentity
from hivemind.honey_store.nectar import NectarIntake
from hivemind.honey_store.ripening import Ripener
from hivemind.honey_store.store import HoneyStore
from hivemind.manifest import HoneyClearanceSection, HoneyRetrievalSection, HoneyRipeningSection

__all__ = ["HoneyAccess"]


@dataclass(frozen=True, slots=True)
class HoneyAccess:
    """One Hive's Honey Store, ready to use: its store, doors and the policy they run under.

    Attributes:
        store: The durable store every other handle here reads and writes.
        intake: The one door a deposit comes in through (Waggle chunks, in-process submissions).
        retriever: Answers a query for any reader, within its scope, clearance and budget.
        ripener: Ripens pending Nectar and embeds rows still lacking a vector (the House Bee's
            duty; `hive honey ripen --now` and `reembed` run it on demand).
        identity: The Hive, node and actor every event from these handles is stamped with.
        retrieval: `[honey.retrieval]`: weights, floors, budget fraction and pre-check size.
        ripening: `[honey.ripening]`: pass sizes and pace, for whoever schedules the Ripener.
        clearance: `[honey.clearance]`: the default label and the per-tier read matrix a caller
            passes to `hivemind.honey_store.clearance.reader_ceiling`.
    """

    store: HoneyStore
    intake: NectarIntake
    retriever: HoneyRetriever
    ripener: Ripener
    identity: HoneyIdentity
    retrieval: HoneyRetrievalSection
    ripening: HoneyRipeningSection
    clearance: HoneyClearanceSection
