"""Bee Bread: the warm memory tier (roadmap step 4.2).

An index over Brood Chamber history and the Pheromone Trail by id, time and task, plus stored
Handoffs and deposited transcripts -- lookup only, no search (codingrules section 8.9). `entry`
defines the one row shape (`BeeBreadEntry`, `BeeBreadEntryKind`); `index` is the lookup-only reader
(`BeeBread`); `deposit` is every write path, including the transcript deposit
`hivemind.memory.checkpoint.write_checkpoint` calls and the archive `hivemind.memory.demote.demote`
calls when an item leaves hot state.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Used by hivemind.memory.checkpoint and
    hivemind.memory.demote (both call into `deposit`); constructed (BeeBread) by whichever
    composition root wires up warm-tier lookups. Calls into hivemind.cell, hivemind.memory.context,
    hivemind.memory.errors, hivemind.memory.hot_state, hivemind.memory.notes, hivemind.memory.pins,
    hivemind.memory.relevance, hivemind.memory.store (protocol only, TYPE_CHECKING), hivemind.
    pheromone and waggle.

Key invariants:
    - Every entry carries a HoneyClearance and every BeeBread lookup filters by the reader's
      allowance (codingrules section 8.9).
    - Whatever is not in `__all__` is private to this package (codingrules section 5.4).

See Also:
    - .claude/codingrules.md section 8.9 for the Bee Bread tier this package implements.
    - .claude/roadmap.md step 4.2 for this package's field-by-field and method-by-method spec.
    - hivemind.memory.store for the memory_bee_bread table this package's writes and reads go
      through.

Public API:
    - BeeBreadEntry, BeeBreadEntryKind, MAX_ENTRY_TEXT_CHARS, MAX_ENTRY_PAYLOAD_CHARS,
      MAX_REF_IDS: the one row shape this tier stores (entry).
    - BeeBread: the lookup-only reader, by id, by task, between two times (index).
    - deposit_transcript, deposit_tool_result, deposit_handoff_ref, deposit_recording_ref,
      deposit_hot_state_item, deposit_dropped_items: every write path into this tier (deposit).
"""

from hivemind.memory.bee_bread.deposit import (
    deposit_dropped_items,
    deposit_handoff_ref,
    deposit_hot_state_item,
    deposit_recording_ref,
    deposit_tool_result,
    deposit_transcript,
)
from hivemind.memory.bee_bread.entry import (
    MAX_ENTRY_PAYLOAD_CHARS,
    MAX_ENTRY_TEXT_CHARS,
    MAX_REF_IDS,
    BeeBreadEntry,
    BeeBreadEntryKind,
)
from hivemind.memory.bee_bread.index import BeeBread

__all__ = [
    "MAX_ENTRY_PAYLOAD_CHARS",
    "MAX_ENTRY_TEXT_CHARS",
    "MAX_REF_IDS",
    "BeeBread",
    "BeeBreadEntry",
    "BeeBreadEntryKind",
    "deposit_dropped_items",
    "deposit_handoff_ref",
    "deposit_hot_state_item",
    "deposit_recording_ref",
    "deposit_tool_result",
    "deposit_transcript",
]
