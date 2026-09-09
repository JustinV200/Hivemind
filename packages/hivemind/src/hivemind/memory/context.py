"""Define MemoryIdentity and MemoryContext: the three collaborators every memory write shares.

Every function in this package that changes durable state (`hivemind.memory.checkpoint.
write_checkpoint`, `hivemind.memory.pins.add_pin`, `hivemind.memory.notes.add_note`,
`hivemind.memory.episodes.record_episode`) needs the same three things: a `MemoryStore` to write
into, an identity to stamp on the accompanying `MemoryEvent`, and a `Clock` to mint ids and
timestamps from. There is no separate `PheromoneTrail` field here, on purpose: every `MemoryStore`
write method already takes the event to insert and records it in the same transaction as the row
it writes (codingrules section 12), exactly the way `hivemind.brood_chamber.store.sqlite.
SqliteTaskStore` and its in-memory counterpart do -- the trail is the store's own concern, not a
second thing this bundle's callers write to directly. Bundling the three into one `MemoryContext`
keeps every writer function in this package at three parameters or fewer (codingrules section
5.1's parameter limit), the same role `hivemind.brood_chamber.chamber.base.ChamberIdentity`/
`_ChamberBase` plays for the Brood Chamber -- and this module mirrors that shape field for field.
`MemoryContext`'s `store` field is typed only under `TYPE_CHECKING`: `hivemind.memory.store.
protocol` imports `Pin`, `Note`, `Handoff` and `EpisodeRecord` to shape its own Protocol methods,
and those modules in turn import `MemoryContext` from here, so a real (non-`TYPE_CHECKING`) import
of the store protocol here would close that cycle. `from __future__ import annotations` already
makes every annotation in this module a string at runtime, so the dataclass never needs to resolve
`MemoryStore` to a real object -- only a type checker does, and `TYPE_CHECKING` is exactly the
guard that keeps that import out of the runtime import graph.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Constructed by whichever composition
    root wires up a Worker's or Warden's memory access; passed into every write function in this
    package. Calls into waggle only at runtime; hivemind.memory.store.protocol only under
    TYPE_CHECKING.

Key invariants:
    - MemoryIdentity mirrors hivemind.brood_chamber.chamber.base.ChamberIdentity's shape exactly
      (hive_id, node_id, actor), so the two subsystems stamp trail events the same way.
    - MemoryContext carries no logic of its own: it is a plain bundle of collaborators, never
      mutated after construction (frozen, slots).

See Also:
    - .claude/codingrules.md section 5.1 for the parameter-count limit this bundle exists to keep.
    - .claude/codingrules.md section 12 for the same-transaction rule every writer in this package
      relies on its MemoryStore to uphold.
    - hivemind.brood_chamber.chamber.base for ChamberIdentity/_ChamberBase, the pattern this
      module mirrors field for field.
    - hivemind.memory.store.protocol for MemoryStore, the protocol `store` is typed against.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from waggle.clock import Clock
from waggle.ids import HiveId, NodeId

if TYPE_CHECKING:
    # Type-checking only: see the module docstring for why a real import here would be circular.
    from hivemind.memory.store.protocol import MemoryStore

__all__ = ["MemoryContext", "MemoryIdentity"]


@dataclass(frozen=True, slots=True)
class MemoryIdentity:
    """The Hive, node and actor a memory writer stamps on every MemoryEvent it records.

    Attributes:
        hive_id: The Hive this identity's events belong to.
        node_id: This process's own node id (a Queen or a Warden), carried on every event so a
            merged trail can tell which node recorded it.
        actor: Who this identity acts as: a bee id, or the literal "human" or "system"
            (`hivemind.pheromone.events.base.ACTOR_LITERALS`).
    """

    hive_id: HiveId
    node_id: NodeId
    actor: str


@dataclass(frozen=True, slots=True)
class MemoryContext:
    """The store, identity and clock every memory-writing function shares.

    Attributes:
        store: Where every Pin, Note, Handoff and EpisodeRecord this context touches is persisted;
            each write method also records the accompanying MemoryEvent in the same transaction
            (codingrules section 12), so no separate trail field is needed here.
        identity: The Hive, node and actor stamped on every event this context's writes record.
        clock: Injected time source for every id minted and every timestamp written.
    """

    store: MemoryStore
    identity: MemoryIdentity
    clock: Clock
