"""Define PheromoneEvent, the base every Pheromone Trail event family subclasses.

A PheromoneEvent is one entry on the Pheromone Trail, the Hive's append-only audit log
(codingrules section 12): who (`actor`) did what (`kind`) to what (`subject_id`), when (`at`),
recorded from which Hive and which node. This module defines the shape and the validation every
family shares; `hivemind.pheromone.events.families` supplies one subclass per event family
(`cell.*`, `task.*`, ...), each fixing its own `FAMILY` and `KINDS`. Splitting the shared base
from the family list keeps both modules under the codingrules 5.1 size limit (codingrules 5.2:
"a concept that needs a second file becomes a package"). `LlmUsage` lives here too: it is the
provider-neutral token-and-cost shape codingrules 8.6 requires every LLM adapter to normalise its
response into, and `hivemind.pheromone.events.families.LlmEvent` is the one place it rides on a
trail event.

The `kind` and `subject_id` validators below use two different waggle id concepts on purpose.
`actor` is a Hive-internal principal (a bee, or the literal "human"/"system"), so it is checked
against a fixed, short list of `IdKind` members. `subject_id` names whatever the event is about --
a Task, a Cell, a Tool, anything the Hive tracks -- so it accepts any `IdKind` member at all; the
validator tries each prefix in turn rather than requiring the caller to say which kind it expects.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Subclassed by every family in
    hivemind.pheromone.events.families; instances are produced by every layer above Layer 1 each
    time it mutates state, and consumed by hivemind.pheromone.trail (phase 2.2) and the
    Observation Hive. Calls into waggle.messages.base and waggle.ids only.

Key invariants:
    - PheromoneEvent itself is frozen (`model_config`) but not hashable: `payload` is a `dict`,
      which pydantic cannot hash, so no `__hash__` is defined and `hash(event)` raises TypeError.
    - The base class's `FAMILY` is `""` and `KINDS` is empty, so `_validate_kind` rejects every
      `kind` on a bare `PheromoneEvent`; a family subclass becomes constructible only by setting
      both class variables to a matching, non-empty vocabulary (see families.py).
    - No field on this model or LlmUsage ever carries prompt or completion text (codingrules
      section 12); `payload`'s own validator enforces that at the value level via
      FORBIDDEN_PAYLOAD_KEYS.

See Also:
    - .claude/codingrules.md section 12 for the Pheromone Trail rules this module follows.
    - .claude/codingrules.md section 8.6 for the Usage shape LlmUsage mirrors.
    - hivemind.pheromone.events.families for the family subclasses, the vocabulary, and the JSON
      codec (parse_event / parse_event_json) built on this base.
    - waggle.messages.base for the `<Kind>IdField` aliases and UtcDatetime this module reuses.
"""

from __future__ import annotations

import json
import re
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from waggle.ids import IdKind
from waggle.messages.base import EventIdField, HiveIdField, NodeIdField, UtcDatetime, check_id

# <family>.<snake_name>: matches waggle's own KIND_PATTERN shape (waggle.messages.base) so a
# Pheromone kind and a Waggle message kind read the same way in logs and on the trail.
KIND_PATTERN = r"^[a-z_]+\.[a-z_]+$"
MAX_ACTOR_CHARS = 64  # A literal ("human"/"system") or a prefixed ULID; never longer than this.
MAX_PAYLOAD_STRING_CHARS = 1_000  # A payload value is an id, a path or a short label, not prose.
MAX_PAYLOAD_BYTES = 16_384  # 16 KiB: generous for a handful of ids and numbers, never a transcript.
ACTOR_LITERALS = frozenset({"human", "system"})  # The two actors on the trail that are not bees.
# The kinds of id an `actor` may be: a Hive-level bee or device, never a Task, Tool or other
# non-principal id -- codingrules 6.1's actor set for the Pheromone Trail.
_ACTOR_ID_KINDS = (IdKind.HIVE, IdKind.WARDEN, IdKind.WORKER, IdKind.DEVICE)
# codingrules section 12: "No event ever carries prompt or completion text." Checked
# case-insensitively at any payload depth so "Prompt", "PROMPT" and "messages" are all refused.
FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "prompt",
        "completion",
        "messages",
        "history",
        "transcript",
        "content",
        "text",
        "output",
        "screenshot",
    }
)

__all__ = [
    "ACTOR_LITERALS",
    "FORBIDDEN_PAYLOAD_KEYS",
    "KIND_PATTERN",
    "MAX_ACTOR_CHARS",
    "MAX_PAYLOAD_BYTES",
    "MAX_PAYLOAD_STRING_CHARS",
    "LlmUsage",
    "PheromoneEvent",
]


class PheromoneEvent(BaseModel):
    """One entry on the Pheromone Trail: who did what to what, when, from which node.

    Not hashable despite `frozen=True`: `payload` is a `dict`, which pydantic cannot hash, so no
    `__hash__` is defined here and calling `hash()` on an instance raises `TypeError`. Never
    instantiate this class directly outside a test fixture; every real event is one of the family
    subclasses in `hivemind.pheromone.events.families`, each of which sets `FAMILY` and `KINDS` to
    a matching, non-empty vocabulary. `FAMILY` is `""` and `KINDS` is empty here, so the `kind`
    validator rejects every value on the base class -- the deliberate way this base is made
    uninstantiable-in-practice without abstract-class machinery.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Set by each family subclass (families.py); the base's own "" / empty pair matches no kind.
    FAMILY: ClassVar[str] = ""
    KINDS: ClassVar[frozenset[str]] = frozenset()

    id: EventIdField = Field(description="This event's own id; unique, orders it within a node.")
    hive_id: HiveIdField = Field(description="The Hive this event was recorded on.")
    node_id: NodeIdField = Field(
        description="The node (Queen or Warden) whose segment this event was written to."
    )
    at: UtcDatetime = Field(
        description="When the recorded action happened, from an injected Clock."
    )
    actor: str = Field(
        max_length=MAX_ACTOR_CHARS,
        description=(
            "Who did it: a hive_/warden_/worker_/device_ id, or the literal 'human' or 'system'."
        ),
    )
    kind: str = Field(
        description=(
            "The stable audit vocabulary string, '<family>.<name>'; must be one of the "
            "subclass's own KINDS and share its FAMILY."
        )
    )
    subject_id: str = Field(
        description="The id of whatever this event is about; any waggle IdKind is accepted."
    )
    payload: dict[str, JsonValue] = Field(
        default_factory=dict,
        description=(
            "Structured, non-text extra data: ids, counts, reasons. Never prompt or completion "
            "text (codingrules section 12); FORBIDDEN_PAYLOAD_KEYS and the size limits enforce it."
        ),
    )

    @property
    def family(self) -> str:
        """Return the family segment of `kind`, the text before the dot.

        Returns:
            `kind` up to (not including) its first `.`, e.g. `"task"` for `"task.submitted"`.
        """
        # kind is already validated to contain exactly one dot by _validate_kind, so partition's
        # first result is always the family segment.
        family, _, _ = self.kind.partition(".")
        return family

    @field_validator("actor")
    @classmethod
    def _validate_actor(cls, value: str) -> str:
        """Accept a literal actor or a well-formed id of one of the four principal kinds."""
        # The two non-bee actors are checked first and exactly, before any id parsing is tried.
        if value in ACTOR_LITERALS:
            return value
        for kind in _ACTOR_ID_KINDS:
            if value.startswith(f"{kind.value}_"):
                return check_id(value, kind)
        accepted = ", ".join(sorted({*ACTOR_LITERALS, *(f"{k.value}_*" for k in _ACTOR_ID_KINDS)}))
        raise ValueError(f"actor {value!r} is none of the accepted forms: {accepted}.")

    @field_validator("kind")
    @classmethod
    def _validate_kind(cls, value: str) -> str:
        """Require `<family>.<name>` shape, membership in `cls.KINDS`, and a matching family."""
        # Shape first: a malformed kind can never be "one of KINDS" in any meaningful sense, so
        # reporting the shape problem is more useful than a plain "not a known kind" message.
        if not re.fullmatch(KIND_PATTERN, value):
            raise ValueError(f"kind {value!r} is not '<family>.<name>' shaped ({KIND_PATTERN}).")
        if value not in cls.KINDS:
            raise ValueError(f"kind {value!r} is not one of {cls.__name__}'s known kinds.")
        family, _, _ = value.partition(".")
        # Belt and suspenders: KINDS should already be family-pure, but a family subclass whose
        # KINDS was hand-edited wrong is exactly the bug this second check catches at test time.
        if family != cls.FAMILY:
            raise ValueError(f"kind {value!r} belongs to family {family!r}, not {cls.FAMILY!r}.")
        return value

    @field_validator("subject_id")
    @classmethod
    def _validate_subject_id(cls, value: str) -> str:
        """Accept `value` when it is a well-formed id of any waggle IdKind."""
        for kind in IdKind:
            if value.startswith(f"{kind.value}_"):
                return check_id(value, kind)
        raise ValueError(f"subject_id {value!r} does not start with a known IdKind prefix.")

    @field_validator("payload")
    @classmethod
    def _validate_payload(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        """Reject a forbidden key, an oversized string, or an oversized payload at any depth."""
        _check_payload_node(value)
        encoded_size = len(json.dumps(value, ensure_ascii=False).encode())
        if encoded_size > MAX_PAYLOAD_BYTES:
            raise ValueError(
                f"payload is {encoded_size} bytes serialised, over the {MAX_PAYLOAD_BYTES}-byte "
                "limit."
            )
        return value


class LlmUsage(BaseModel):
    """The provider-neutral token-and-cost shape every LLM adapter normalises its usage into.

    Codingrules 8.6: "Usage is normalised. Every LLMResponse carries a Usage (input, output,
    cached tokens, and cost when the manifest lists a price for that model). The Pheromone Trail
    and the cost view read only this normalised value, so provider changes never break
    accounting." This is that shape's copy on the trail, carried by an `llm.call` event
    (`hivemind.pheromone.events.families.LlmEvent`); it never carries a raw provider response.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_tokens: int = Field(ge=0, description="Tokens the request consumed, as billed.")
    output_tokens: int = Field(ge=0, description="Tokens the response generated, as billed.")
    cached_tokens: int = Field(
        ge=0, description="Of input_tokens, how many were served from a provider prompt cache."
    )
    cost_usd: float = Field(ge=0, description="What this call cost in US dollars, 0 if unpriced.")


def _check_payload_node(node: object) -> None:
    """Recursively enforce the forbidden-key and string-length rules on one payload node.

    Args:
        node: A JsonValue: a dict, a list, or a scalar (str, int, float, bool or None).

    Raises:
        ValueError: A dict key (case-insensitive) is in FORBIDDEN_PAYLOAD_KEYS, or a string value
            is longer than MAX_PAYLOAD_STRING_CHARS, at any nesting depth.
    """
    if isinstance(node, dict):
        # Every key is checked before descending into its value, so the first violation found is
        # always the shallowest one -- a more useful error than the deepest.
        for key, child in node.items():
            if key.lower() in FORBIDDEN_PAYLOAD_KEYS:
                raise ValueError(
                    f"payload key {key!r} is forbidden: it could carry prompt or completion "
                    "text (codingrules section 12: 'no event ever carries text')."
                )
            _check_payload_node(child)
    elif isinstance(node, list):
        for child in node:
            _check_payload_node(child)
    elif isinstance(node, str) and len(node) > MAX_PAYLOAD_STRING_CHARS:
        raise ValueError(
            f"payload string value is {len(node)} chars, over the {MAX_PAYLOAD_STRING_CHARS}-char "
            "limit."
        )
