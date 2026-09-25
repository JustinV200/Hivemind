"""Define HoneyEvent: the event family of the Honey Store, the Hive's cold knowledge tier.

One of the Pheromone Trail's fourteen event families records the Honey Store (roadmap phase 7,
ADR-0035): Nectar (raw deposits) arriving, ripening into Honey (the distilled, searchable rows),
labels moving, and queries being answered. `HoneyEvent` is a `PheromoneEvent` subclass
(`hivemind.pheromone.events.base`) fixing `FAMILY` and `KINDS`.

Vocabulary (family -> kind -> when it is recorded):
    honey: nectar_received (intake stored a new Nectar deposit, roadmap step 7.4); nectar_
        deduplicated (a deposit matched a stored one by content or source key; the stored label
        was raised if the new one was higher, never lowered); nectar_rejected (intake refused a
        deposit or one of its chunks: over the size cap, a bad offset, a digest mismatch, too many
        open deposits, or a Night Veil deposit it may not accept); ripened (the House Bee turned one
        Nectar into Honey rows, roadmap step 7.5); ripen_failed (one Nectar could not be ripened
        this pass and stays pending); reembedded (one pass embedded pending Honey rows for the
        current embedding model, ADR-0036); label_raised (intake, a dedupe merge or the ripener
        raised a label); label_lowered (a judge verdict or the human lowered one, with the
        approver); retired (a Honey row was superseded and no longer returned); queried (a Honey
        query was answered, with hit, withheld and token counts, never the query or hit text);
        note_proposed (the human proposed a note from a Honey folder, queued for the Queen,
        roadmap step 7.10); vectors_pruned (`hive honey reembed --prune` dropped every other
        embedding model's vectors once every live row had one for the kept model, ADR-0037;
        payload the kept model plus how many other models were dropped and their combined row
        count -- the exact per-model breakdown lives only in the caller's own returned
        `PruneOutcome`, never on the trail, so this vocabulary's payloads stay flat like every
        other honey.* kind's). Every honey.* kind is recorded on the Queen's own node and survives
        a Night Veil teardown carrying ids and counts only; a Night Veil Cell's ephemeral Nectar
        and a Night Veil reader's query are never recorded at all, so nothing about either can
        outlive the teardown (ADR-0035).

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.pheromone.events.
    families`. Constructed by `hivemind.honey_store` for every write it makes and every query it
    answers; decoded by `hivemind.pheromone.events.families.codec`. Calls into
    `hivemind.pheromone.events.base` only.

Key invariants:
    - Every kind in KINDS starts with "honey.", this class's own FAMILY.
    - No honey.* payload ever carries a deposit's content, a hit's text or a query's words.

See Also:
    - docs/adr/0035-honey-store-sqlite-fts5-sqlite-vec.md for the store these events record.
    - hivemind.pheromone.events.families.codec for EVENT_FAMILIES and the JSON codec.
"""

from __future__ import annotations

from typing import ClassVar

from hivemind.pheromone.events.base import PheromoneEvent

__all__ = ["HoneyEvent"]


class HoneyEvent(PheromoneEvent):
    """A Honey Store intake, ripening, labelling or query event; see the module's `honey` entry.

    Roadmap phase 7 (ADR-0035): recorded by `hivemind.honey_store` for every write it makes to
    Nectar or Honey and for every query it answers, always with ids and counts in the payload and
    never a deposit's content, a hit's text or a query's words (codingrules section 12).
    """

    FAMILY: ClassVar[str] = "honey"
    KINDS: ClassVar[frozenset[str]] = frozenset(
        {
            "honey.nectar_received",
            "honey.nectar_deduplicated",
            "honey.nectar_rejected",
            "honey.ripened",
            "honey.ripen_failed",
            "honey.reembedded",
            "honey.label_raised",
            "honey.label_lowered",
            "honey.retired",
            "honey.queried",
            "honey.note_proposed",
            # ADR-0037: the operator's own word, never automatic.
            "honey.vectors_pruned",
        }
    )
