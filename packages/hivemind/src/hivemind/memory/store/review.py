"""Render a taintable memory item as the text a taint judge reviews: its own words, nothing more.

The taint judge (`hivemind.memory.taint.judge`, roadmap step 10.6d) decides whether a tainted item
may reach prompts again from the item alone, "with no shared context" (ADR-0043). This module is
what "the item alone" means for each kind the memory tables hold: a Handoff's goal, progress,
decisions, lists and notes; an episode record's trigger (the outside words it carried included,
since those are what an injection would ride in on), reasoning summary, decision and action; a
Bee Bread entry's preview or payload. It deliberately leaves out who wrote the item (`written_by`,
`principal`) and which task it served: the judge reviews the text, never the bee. Both memory
stores call it, so the in-memory and the SQLite store always show the judge the same thing.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.memory.store`. Called
    by `hivemind.memory.store.memory.InMemoryMemoryStore` and `hivemind.memory.store.sqlite.taint`
    for `TaintLedger.read_taintable`. Calls into `hivemind.memory.bee_bread.entry`,
    `hivemind.memory.episodes` and `hivemind.memory.handoff` only.

Key invariants:
    - Pure: the same item always renders the same text.
    - Never includes the author's id, the task id, the clearance or the taint label itself.

See Also:
    - hivemind.memory.taint.ledger for TaintableItem.content, the field this fills.
    - hivemind.memory.taint.judge for how the text is shown to the judge.
"""

from __future__ import annotations

from collections.abc import Sequence

from hivemind.memory.bee_bread.entry import BeeBreadEntry
from hivemind.memory.episodes import EpisodeRecord
from hivemind.memory.handoff import Handoff

__all__ = ["entry_review_text", "episode_review_text", "handoff_review_text", "review_text"]


def review_text(item: Handoff | EpisodeRecord | BeeBreadEntry) -> str:
    """Render any taintable item the memory tables hold, by its kind.

    Args:
        item: A stored Handoff, episode record or Bee Bread entry.

    Returns:
        The text a taint judge reviews for it.
    """
    if isinstance(item, Handoff):
        return handoff_review_text(item)
    if isinstance(item, EpisodeRecord):
        return episode_review_text(item)
    return entry_review_text(item)


def handoff_review_text(handoff: Handoff) -> str:
    """Render every field a resuming bee would read from `handoff`, as plain labelled lines.

    Args:
        handoff: The stored Handoff.

    Returns:
        Its goal, progress, decisions with reasons, every list field and its notes.
    """
    lines = [f"Goal: {handoff.goal}", f"Progress: {handoff.progress}"]
    lines += [
        f"Decision: {decision.what} (because {decision.why})" for decision in handoff.decisions
    ]
    # Every list field, in the order a resumed prompt shows them, so the judge sees what the next
    # bee would be told to do.
    lines += _listed("Next steps", handoff.next_steps)
    lines += _listed("Do not redo", handoff.do_not_redo)
    lines += _listed("Tried and failed", handoff.tried_and_failed)
    lines += _listed("Constraints", handoff.constraints)
    lines += _listed("Open threads", handoff.open_threads)
    lines += _listed("Pinned facts", handoff.pinned_facts)
    if handoff.notes:
        lines.append(f"Notes: {handoff.notes}")
    return "\n".join(lines)


def episode_review_text(record: EpisodeRecord) -> str:
    """Render one episode record's trigger, reasoning and decision as plain labelled lines.

    Args:
        record: The stored episode record.

    Returns:
        Its trigger (with any outside words it carried), reasoning summary, decision and action.
    """
    lines = [f"Trigger ({record.trigger.kind}): {record.trigger.summary}"]
    if record.trigger.untrusted is not None:
        lines.append(f"Outside words in the trigger: {record.trigger.untrusted.text}")
    if record.reasoning_summary:
        lines.append(f"Reasoning summary: {record.reasoning_summary}")
    lines += [f"Decision: {record.decision}", f"Action: {record.action}"]
    return "\n".join(lines)


def entry_review_text(entry: BeeBreadEntry) -> str:
    """Render one Bee Bread entry's own content: its payload when it holds one, else its preview.

    Args:
        entry: The stored Bee Bread entry.

    Returns:
        Its kind and its payload or preview text; an index-only entry with neither says so.
    """
    body = entry.payload if entry.payload is not None else entry.text
    return f"{entry.kind.value}: {body if body else '(an index entry with no text of its own)'}"


def _listed(label: str, items: Sequence[str]) -> list[str]:
    """Render one labelled list, or nothing when it is empty."""
    return [f"{label}:", *(f"- {item}" for item in items)] if items else []
