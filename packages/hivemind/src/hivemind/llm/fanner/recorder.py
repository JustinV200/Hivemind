"""Define LlmEventRecorder: how the Fanner reports a completed call or a spill to the trail.

The Fanner (`hivemind.llm.fanner.lane.FannerLane`, roadmap step 3.12a) is the seat meter every
model call passes through, and codingrules section 12 requires that every state-changing action --
a completed call, a spill from one binding to the next -- write a Pheromone Trail event (the Hive's
append-only audit log) before it counts as done. This module is that seam: `LlmEventRecorder` is a
one-method Protocol a lane calls with a raw `(kind, subject_id, payload)` triple, so `FannerLane`
never constructs an `LlmEvent` (`hivemind.pheromone.events.families`) directly and never knows
whether anything is listening. `TrailLlmEventRecorder` is the implementation that actually builds
one; `NullLlmEventRecorder` is the default that discards every occurrence, mirroring
`hivemind.llm.ladders.observer.NullLadderObserver`'s own shape.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.fanner`. Called by
    `hivemind.llm.fanner.lane.FannerLane` once per completed call (`llm.call`) and once per spill
    (`llm.spill`). Calls into `hivemind.llm.models` (for `JsonObject`), `hivemind.pheromone` (for
    `LlmEvent`, `LlmUsage` and `PheromoneTrail`) and `waggle` only.

Key invariants:
    - `LlmEvent` fixes `slot`, `provider` and `usage` as typed fields, not payload entries
      (`hivemind.pheromone.events.families.LlmEvent`'s own docstring); `TrailLlmEventRecorder`
      lifts those three keys out of the incoming `payload` when present and leaves everything else
      (`latency_s`, `from_binding`, `to_binding`, `reason`, ...) in the event's own `payload` dict.
    - `subject_id` must name a well-formed waggle id (any `IdKind` prefix), but a Fanner occurrence
      has no id of its own to point at yet: no `CallId` `IdKind` exists (the same gap
      `hivemind.llm.ladders.observer.TrailLadderObserver` already flags for `llm.fallback`).
      `TrailLlmEventRecorder` does not mint the subject_id itself -- the caller (`FannerLane`)
      does, with `waggle.ids.new_event_id`, so this module stays a pure "given a subject_id, record
      it" seam; this is flagged again in this dispatch's report as an open question for whichever
      later dispatch gives a call a real id.

See Also:
    - .claude/codingrules.md section 8.10 for the Fanner's role as "the only place seat counts are
      enforced" and its trail-recording responsibility.
    - .claude/codingrules.md section 12 for the Pheromone Trail payload rules this recorder
      follows (ids and enum values only, never text).
    - hivemind.llm.ladders.observer for TrailLadderObserver, the sibling seam this one mirrors.
    - hivemind.pheromone.events.families.LlmEvent for the trail event shape this recorder builds.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import JsonValue

from hivemind.llm.models import JsonObject
from hivemind.pheromone import LlmEvent, LlmUsage, PheromoneTrail
from waggle.clock import Clock
from waggle.ids import HiveId, NodeId, new_event_id

# The three LlmEvent fields the family fixes as typed columns rather than payload entries
# (hivemind.pheromone.events.families.LlmEvent); TrailLlmEventRecorder lifts these out of a raw
# payload dict on the way to building the event, leaving the rest as that event's own payload.
_TYPED_FIELD_KEYS = ("slot", "provider", "usage")

__all__ = ["LlmEventRecorder", "NullLlmEventRecorder", "TrailLlmEventRecorder"]


class LlmEventRecorder(Protocol):
    """Record one Fanner occurrence, without the Fanner knowing whether anything is listening."""

    async def record(self, kind: str, subject_id: str, payload: JsonObject) -> None:
        """Record `kind` (an LlmEvent kind) about `subject_id`, carrying `payload`.

        Args:
            kind: The LlmEvent kind string: `"llm.call"` or `"llm.spill"`.
            subject_id: A well-formed waggle id standing in for this occurrence (see the module
                docstring's "Key invariants" for why the caller mints a fresh one rather than
                naming a real entity).
            payload: Extra fields for this occurrence. A Trail-backed implementation lifts
                `"slot"`, `"provider"` and `"usage"` out into `LlmEvent`'s own typed fields when
                present, leaving everything else on the recorded event's `payload`.
        """
        ...


class NullLlmEventRecorder:
    """An LlmEventRecorder that discards every occurrence; the Fanner's default when none is set."""

    async def record(self, kind: str, subject_id: str, payload: JsonObject) -> None:
        """Discard the occurrence; see `LlmEventRecorder.record` for the full contract."""
        return None


class TrailLlmEventRecorder:
    """Record every Fanner occurrence as an `LlmEvent` on the Pheromone Trail."""

    def __init__(
        self, trail: PheromoneTrail, hive_id: HiveId, node_id: NodeId, actor: str, clock: Clock
    ) -> None:
        """Wire the trail-writer identity every recorded event is stamped with.

        Args:
            trail: Where the built `LlmEvent` is recorded.
            hive_id: The Hive the recorded event belongs to.
            node_id: This process's own node id (a Queen or a Warden).
            actor: Who this recorder acts as: a bee id, or the literal "human" or "system"
                (`hivemind.pheromone.events.base.ACTOR_LITERALS`).
            clock: Injected clock for the event's id and timestamp.
        """
        self._trail = trail
        self._hive_id = hive_id
        self._node_id = node_id
        self._actor = actor
        self._clock = clock

    async def record(self, kind: str, subject_id: str, payload: JsonObject) -> None:
        """Build and record the `LlmEvent` for one occurrence; see `LlmEventRecorder.record`."""
        typed, remaining = _split_typed_fields(payload)
        event = LlmEvent(
            id=new_event_id(self._clock),
            hive_id=self._hive_id,
            node_id=self._node_id,
            at=self._clock.now(),
            actor=self._actor,
            kind=kind,
            subject_id=subject_id,
            slot=_as_optional_str(typed.get("slot")),
            provider=_as_optional_str(typed.get("provider")),
            usage=_build_usage(typed.get("usage")),
            payload=remaining,
        )
        await self._trail.record(event)


def _split_typed_fields(payload: JsonObject) -> tuple[JsonObject, JsonObject]:
    """Split `payload` into LlmEvent's typed fields (slot/provider/usage) and everything else.

    Args:
        payload: The raw occurrence payload `FannerLane` built.

    Returns:
        A `(typed, remaining)` pair: `typed` holds whichever of `_TYPED_FIELD_KEYS` `payload`
        set, `remaining` holds every other key, unchanged.
    """
    typed = {key: payload[key] for key in _TYPED_FIELD_KEYS if key in payload}
    remaining = {key: value for key, value in payload.items() if key not in _TYPED_FIELD_KEYS}
    return typed, remaining


def _as_optional_str(value: JsonValue | None) -> str | None:
    """Narrow a JsonValue that is either a string or absent, for LlmEvent's slot/provider fields."""
    return value if isinstance(value, str) else None


def _build_usage(value: JsonValue | None) -> LlmUsage | None:
    """Build an LlmUsage from a raw usage dict, or None when the payload carried none.

    Args:
        value: `payload["usage"]` as built by `FannerLane`'s own `_call_payload`: a plain dict of
            `input_tokens`, `output_tokens`, `cached_tokens` and `cost_usd`, or absent entirely
            for an occurrence (a spill) that never carries usage.
    """
    if not isinstance(value, dict):
        return None
    return LlmUsage.model_validate(value)
