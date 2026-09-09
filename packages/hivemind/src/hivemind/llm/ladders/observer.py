"""Define LadderObserver: how a degradation ladder reports stepping down a rung or a binding.

A degradation ladder (`hivemind.llm.ladders`, the fallback logic that lets a bee call a model
without knowing its provider's exact capabilities) sometimes has to give up on what it was trying
and try something weaker instead: a lower rung of the same binding (native schema output failed,
try JSON mode), or the next binding in a `hivemind.llm.slots.BoundModel`'s fallback chain (the
current provider is unavailable or rate-limited). Both moments matter to a human operator and to
the Pheromone Trail (the Hive's append-only audit log, `hivemind.pheromone`), so every ladder
reports them through this one `LadderObserver` seam instead of writing a trail event by hand.
`FallbackNote` is the frozen value a ladder builds; `TrailLadderObserver` is the implementation
that turns a note into an `llm.fallback` event; `NullLadderObserver` is the default when no caller
cares to watch.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.ladders`. Called by
    `hivemind.llm.ladders.structured.complete_structured` and
    `hivemind.llm.ladders.tools.run_tool_loop` every time either one steps down. Calls into
    `hivemind.forage.slots` (ModelSlot), `hivemind.pheromone` (LlmEvent, PheromoneTrail) and
    waggle (Clock, ids) only; `hivemind.llm.ladders.structured` is imported only under
    `TYPE_CHECKING` (see Key invariants) so this module never creates a runtime import cycle with
    the one sibling that imports it back.

Key invariants:
    - `FallbackNote.to_binding` is `None` exactly when nothing but the rung changed (the binding
      stayed the same); `from_rung`/`to_rung` are both `None` exactly when nothing but the binding
      changed (a fresh binding restarts the ladder at its own top rung, per
      `hivemind.llm.ladders.structured`'s module docstring). The two kinds of step never mix in
      one note.
    - `Rung` (`hivemind.llm.ladders.structured`) is imported here only under `TYPE_CHECKING`:
      `from __future__ import annotations` turns every annotation in this file into a string, so
      `FallbackNote`'s `Rung`-typed fields never need the real class at import time, which is what
      lets `structured.py` import `FallbackNote` from here at runtime without a cycle.
    - `TrailLadderObserver` records exactly one event kind, `llm.fallback`, with a payload of ids
      and enum values only (codingrules section 12: a trail event never carries text); the
      recorded event's `subject_id` is a freshly minted `EventId`, not a reference to an existing
      entity -- see the module-level `_mint_subject_id` docstring for why.

See Also:
    - .claude/codingrules.md section 8.6 for "degrade by ladder, in one place".
    - .claude/codingrules.md section 12 for the Pheromone Trail payload rules this observer
      follows.
    - docs/adr/0009-structured-output-and-tool-call-degradation-ladders.md for the decision this
      module is part of.
    - hivemind.llm.ladders.structured and hivemind.llm.ladders.tools for the two ladders that
      build FallbackNotes.
    - hivemind.pheromone.events.families.LlmEvent for the trail event shape this observer builds.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol

from hivemind.forage.slots import ModelSlot
from hivemind.pheromone import LlmEvent, PheromoneTrail
from waggle.clock import Clock
from waggle.ids import HiveId, NodeId, new_event_id

if TYPE_CHECKING:
    # Only for static type-checking (see the module docstring's "Key invariants"): this avoids a
    # runtime import cycle, since hivemind.llm.ladders.structured imports FallbackNote from here.
    from hivemind.llm.ladders.structured import Rung

FALLBACK_KIND = "llm.fallback"  # The one LlmEvent kind TrailLadderObserver ever records.

__all__ = [
    "FALLBACK_KIND",
    "FallbackNote",
    "FallbackReason",
    "LadderObserver",
    "NullLadderObserver",
    "TrailLadderObserver",
]


class FallbackReason(Enum):
    """Why a ladder moved off its current rung or binding."""

    RUNG_EXHAUSTED = "RUNG_EXHAUSTED"  # This rung's retries ran out; stepping down to a weaker one.
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"  # The gate raised ProviderUnavailableError.
    RATE_LIMITED = "RATE_LIMITED"  # The gate raised RateLimitedError.


@dataclass(frozen=True, slots=True)
class FallbackNote:
    """One step a ladder took away from what it was trying: a weaker rung, or a fallback binding.

    Exactly one of the two step shapes ever applies (see the module docstring's "Key invariants"):
    a rung step-down leaves `to_binding` `None`; a binding fallback leaves `from_rung`/`to_rung`
    both `None`, since a fresh binding restarts at its own top rung.
    """

    slot: ModelSlot  # Which ModelSlot this call was routed to.
    from_binding: str  # The manifest [llm.slots] key the ladder was using before this step.
    to_binding: str | None  # The key it moved to, or None when only the rung changed.
    from_rung: Rung | None  # The rung it stepped down from, or None when only the binding changed.
    to_rung: Rung | None  # The rung it stepped down to, or None when only the binding changed.
    reason: FallbackReason  # Why this step happened.


class LadderObserver(Protocol):
    """Watch a ladder's fallback steps, without the ladder knowing what watches it."""

    async def on_fallback(self, note: FallbackNote) -> None:
        """Record that a ladder took the step described by `note`.

        Args:
            note: The step just taken.
        """
        ...


class NullLadderObserver:
    """A LadderObserver that discards every note; every ladder's default when none is injected."""

    async def on_fallback(self, note: FallbackNote) -> None:
        """Discard `note`; see `LadderObserver.on_fallback` for the full contract."""
        return None


class TrailLadderObserver:
    """Record every fallback step as an `llm.fallback` LlmEvent on the Pheromone Trail."""

    def __init__(
        self, trail: PheromoneTrail, hive_id: HiveId, node_id: NodeId, actor: str, clock: Clock
    ) -> None:
        """Wire the trail-writer identity every recorded event is stamped with.

        Args:
            trail: Where the `llm.fallback` event is recorded.
            hive_id: The Hive the recorded event belongs to.
            node_id: This process's own node id (a Queen or a Warden).
            actor: Who this observer acts as: a bee id, or the literal "human" or "system"
                (`hivemind.pheromone.events.base.ACTOR_LITERALS`).
            clock: Injected clock for the event's id and timestamp.
        """
        self._trail = trail
        self._hive_id = hive_id
        self._node_id = node_id
        self._actor = actor
        self._clock = clock

    async def on_fallback(self, note: FallbackNote) -> None:
        """Record `note` as an `llm.fallback` LlmEvent; see `LadderObserver.on_fallback`."""
        await self._trail.record(self._build_event(note))

    def _build_event(self, note: FallbackNote) -> LlmEvent:
        """Build the `llm.fallback` LlmEvent for `note`, with an id-and-enum payload only."""
        return LlmEvent(
            id=new_event_id(self._clock),
            hive_id=self._hive_id,
            node_id=self._node_id,
            at=self._clock.now(),
            actor=self._actor,
            kind=FALLBACK_KIND,
            subject_id=_mint_subject_id(self._clock),
            slot=note.slot.value,
            payload={
                "from_binding": note.from_binding,
                "to_binding": note.to_binding,
                "from_rung": note.from_rung.value if note.from_rung is not None else None,
                "to_rung": note.to_rung.value if note.to_rung is not None else None,
                "reason": note.reason.value,
            },
        )


def _mint_subject_id(clock: Clock) -> str:
    """Mint a fresh id to stand in as this fallback event's `subject_id`.

    `PheromoneEvent.subject_id` must name a well-formed waggle id (any `IdKind` prefix), but a
    ladder call has no id of its own to point at: no `CallId` `IdKind` exists yet (the Fanner,
    roadmap step 3.12a, may introduce one once seat-metering needs to correlate calls end to end).
    Minting a fresh `EventId` here -- distinct from the event's own `id` -- gives this occurrence a
    valid, stable handle without inventing a new `IdKind` or touching `hivemind.pheromone` (both
    outside this dispatch's file list); flagged in this step's report as an open question for
    whichever later dispatch adds a real call id.
    """
    return new_event_id(clock)
