"""Define FloorGrade and the helpers behind ``[llm.floors]``: each slot's minimum model grade.

A **grade floor** is the weakest Forage-map grade (1 weakest, 5 strongest) a model may have and
still serve a slot: routing (``hivemind.llm.routing``) refuses to prefer a source below it, and a
task's tempo (how fast it must be done and how right it must be) can raise it but never lower it.
The ``[llm.floors]`` table maps a slot's manifest key (``"queen"``, ``"worker"``) to its floor;
this module holds what that table needs apart from ``LlmSection`` itself, which would otherwise
outgrow its size limit: the bounded grade type, the check that every key names a real slot, and
the completion of a partial table with the shipped defaults. The defaults are the Hive's own
opinion of each role: the Queen wants the strongest model the Hive can reach (5, which routing
caps at the highest grade actually on the Forage map, so a Hive with nothing at 5 still works),
the cheap batch roles (Attendant, Ripener, Embedder, Transcriber) accept anything (1), and every
other slot wants at least a second-rung model (2). ``hivemind.llm.routing.default_floors`` holds
the same table for callers that never load a manifest; a test keeps the two in step, because
``hivemind.manifest`` and ``hivemind.llm`` are independent Layer 1 siblings that may not import
each other.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Called by
    ``hivemind.manifest.schema.llm.LlmSection`` (its validator and ``grade_floors``). Calls into
    ``hivemind.forage`` (for ``ModelSlot``) and ``waggle`` (the grade bounds) only.

Key invariants:
    - ``complete_floors`` is total: its result holds one floor for every ``ModelSlot`` member,
      with the operator's value where the table names the slot and the default where it does not.
    - Every floor, default or operator-set, is between ``MIN_MODEL_GRADE`` and ``MAX_MODEL_GRADE``.

See Also:
    - .claude/roadmap.md step 8.1 for "each role has a default grade floor in the manifest".
    - hivemind.llm.routing.rules for the routing-side copy of the defaults and their use.
    - hivemind.manifest.schema.llm for ``LlmSection.floors``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated

from pydantic import Field

from hivemind.forage import ModelSlot
from waggle.messages.forage.values import MAX_MODEL_GRADE, MIN_MODEL_GRADE

__all__ = [
    "DEFAULT_FLOOR",
    "DEFAULT_SLOT_FLOORS",
    "FloorGrade",
    "complete_floors",
    "reject_unknown_floor_keys",
]

# A grade floor: the same closed 1-to-5 range a source's grade uses, so a floor is always a grade
# some source could actually have.
FloorGrade = Annotated[int, Field(ge=MIN_MODEL_GRADE, le=MAX_MODEL_GRADE)]

DEFAULT_FLOOR = 2  # The ordinary slot: a second-rung model or better, since rung 1 is "anything".
_ANYTHING = 1  # The weakest grade: a batch role that accepts whatever model is cheapest.

# The shipped floors for the slots that differ from DEFAULT_FLOOR; every other slot gets it.
DEFAULT_SLOT_FLOORS: Mapping[ModelSlot, int] = {
    ModelSlot.QUEEN: MAX_MODEL_GRADE,  # The strongest model the Hive can reach; routing caps it.
    ModelSlot.ATTENDANT: _ANYTHING,  # Inbox tie-breaks: cheap and fast beats clever.
    ModelSlot.RIPENER: _ANYTHING,  # Batch Nectar-to-Honey work, run in volume.
    ModelSlot.EMBEDDER: _ANYTHING,  # Vectors: a small model is the norm, not a compromise.
    ModelSlot.TRANSCRIBER: _ANYTHING,  # Speech to text, graded by word error rate instead.
}


def reject_unknown_floor_keys(floors: Mapping[str, int]) -> None:
    """Raise when ``floors`` names a key that is not a ``ModelSlot``'s manifest key.

    A named binding (``local_worker``) is a legal ``[llm.slots]`` key but not a slot, and a floor
    belongs to a slot: a floor on a named binding would silently never apply, so it fails loudly.

    Args:
        floors: The ``[llm.floors]`` table as written.

    Raises:
        ValueError: A key names no ``ModelSlot``; the message lists the legal keys.
    """
    legal = {slot.manifest_key for slot in ModelSlot}
    unknown = sorted(set(floors) - legal)
    if unknown:
        raise ValueError(
            f"[llm.floors] names {', '.join(unknown)}, which is not a model slot; "
            f"slots are {sorted(legal)}."
        )


def complete_floors(floors: Mapping[str, int]) -> Mapping[ModelSlot, int]:
    """Return one floor per slot: the table's value where it names the slot, else the default.

    Args:
        floors: The ``[llm.floors]`` table; already checked by ``reject_unknown_floor_keys``.

    Returns:
        A floor, 1 to 5, for every ``ModelSlot`` member.
    """
    return {
        slot: floors.get(slot.manifest_key, DEFAULT_SLOT_FLOORS.get(slot, DEFAULT_FLOOR))
        for slot in ModelSlot
    }
