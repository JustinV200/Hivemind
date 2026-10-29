"""Define cheapest_chat_binding: the configured chat binding that costs the least to call.

The Observation Hive's help assistant (ADR-0069: the Ask on the Help screen) answers a person's
question about the Hive from its own help pages, a small job for which the cheapest model that can
read is the right one. Which model that is must come from the manifest and the Forage map (the
catalogue of every source that can serve a model, with its hand-set or measured grade and its
price), never from a model id in code (codingrules section 8.6). This function ranks every
``[llm.slots]`` row a chat slot can reach, the slot's own row and every fallback in its chain, by
what the map says its source costs: the token price (input plus output, per million) first, then
the lower grade (1 weakest, 5 strongest), then manifest order so a tie is never random. A row the
map holds no source for has no known price, so it ranks after every priced one. The two non-chat
slots, ``EMBEDDER`` (text to vectors) and ``TRANSCRIBER`` (audio to text), are never candidates:
a chat request cannot be made on them.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside ``hivemind.llm.routing``. Called by
    the composition root of ``hive serve`` (``hivemind.cli.compose.help``) once, at start. Calls
    into ``hivemind.forage`` (``SlotBinding``, ``ModelSource``, ``ModelSlot``) and
    ``hivemind.llm.slots`` (``walk_chain``) only.

Key invariants:
    - Pure: no I/O, no clock; the same rows and sources give the same row.
    - The result is always one of ``bindings``, reachable from a chat slot's own key; None only
      when no chat slot has a row at all.
    - A free local source beats any priced hosted one; among free sources the lower grade wins.

See Also:
    - docs/adr/0069-help-ask-answers-from-the-help-pages-on-the-cheapest-binding.md
    - hivemind.llm.routing.policy for the full routing rank this rule is a narrow cousin of.
"""

from __future__ import annotations

from collections.abc import Sequence

from hivemind.forage import ModelSlot, ModelSource, SlotBinding
from hivemind.llm.slots import walk_chain

# Every slot a chat request can be made on: the two non-chat slots answer vectors and transcripts.
CHAT_SLOTS: tuple[ModelSlot, ...] = tuple(
    slot for slot in ModelSlot if slot not in (ModelSlot.EMBEDDER, ModelSlot.TRANSCRIBER)
)

__all__ = ["CHAT_SLOTS", "cheapest_chat_binding"]

# (no source on the map, token price per million, grade, manifest position). Lower sorts first.
_PriceKey = tuple[int, float, int, int]


def cheapest_chat_binding(
    bindings: Sequence[SlotBinding], sources: Sequence[ModelSource]
) -> SlotBinding | None:
    """Return the chat binding the Forage map prices lowest, or None when no chat slot has a row.

    Args:
        bindings: Every ``[llm.slots]`` row, forage-side, in manifest order.
        sources: Every source on the Forage map; a row is priced by the first source naming its
            provider and model.

    Returns:
        The reachable row with the lowest (price, grade, manifest position), unpriced rows last;
        None when ``bindings`` holds no row for any chat slot.

    Raises:
        UnresolvableSlotError: A chain names a row ``bindings`` lacks, or cycles; a loaded
            manifest never does.
    """
    position = {row.key: index for index, row in enumerate(bindings)}
    # Every row a chat slot's chain visits, keyed once however many chains share it.
    reachable: dict[str, SlotBinding] = {}
    for slot in CHAT_SLOTS:
        if slot.manifest_key not in position:
            continue  # The manifest validator makes this unreachable; a test's rows may skip one.
        for row in walk_chain(slot.manifest_key, bindings):
            reachable.setdefault(row.key, row)
    if not reachable:
        return None
    return min(reachable.values(), key=lambda row: _price_key(row, sources, position[row.key]))


def _price_key(row: SlotBinding, sources: Sequence[ModelSource], index: int) -> _PriceKey:
    """Build the sort key for one row: unknown sources last, then price, grade, manifest order."""
    source = next(
        (s for s in sources if s.spec.provider == row.provider and s.spec.model == row.model), None
    )
    if source is None:
        return (1, 0.0, 0, index)
    cost = source.spec.cost
    price = cost.cost_per_million_input_usd + cost.cost_per_million_output_usd
    return (0, price, source.effective_grade, index)
