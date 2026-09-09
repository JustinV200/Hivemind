"""Define ModelCost, ModelSourceSpec and ModelSource: one entry on the Forage map.

The **Forage map** (`hivemind.forage.map.ForageMap`) is the catalogue of every source that can
serve a model, wherever it lives. Each entry is a `ModelSource`, split into a static half and a
live half, because the static half is what an operator writes once in the Hive Manifest's
`[forage.map.<source_id>]` section (a `ModelSourceSpec`) while the live half changes on every
measurement. `ModelSourceSpec` names the model, its provider, its hand-set `grade` from 1 (weakest)
to 5 (strongest) later replaced by a measured score, its context window, its cost (`ModelCost`),
its capability flags and how many seats it offers. The live half is `Distance` (measured latency
and tokens per second from a given Cell -- a unit of compute a Worker runs in -- since a model on
the Hive Stand, the machine the Queen runs on, is farther from a remote device than one on the
device itself) and `Abundance` (free seats, or rate-limit headroom for a hosted provider).
Grade and distance set a floor and a ceiling a task's tempo (how fast it must be done and how right
it must be) must clear before routing may pick a source; abundance says whether one is actually
free right now.

Fits into the Hive:
    Layer 1 (forage; foundational services, capacity as data). Read by `hivemind.forage.map`
    (which owns the map these sources populate) and `hivemind.forage.allocate` (which grants
    bindings against a grade floor and a cost check). `hivemind.manifest` embeds `ModelSourceSpec`
    directly as the model for `[forage.map.<source_id>]`. Calls into `waggle.messages.forage` and
    `waggle.messages.base` only, for the shared bounds and the wire `SourceRef` this module builds.

Key invariants:
    - ModelSourceSpec.grade is between MIN_MODEL_GRADE and MAX_MODEL_GRADE (1 to 5), the same
      bounds `waggle.messages.forage.values` names for the wire form, imported rather than
      repeated so the two can never drift apart.
    - ModelCost's three rates default to 0.0 and are never negative, so a manifest entry that
      leaves a rate unset is free rather than invalid.
    - ModelSource.source_ref() never reads its own `distance` or `abundance`: a SourceRef is the
      wire's *stable* reference to a source, and the receiver resolves live figures from its own
      copy of the map (docs/waggle/spec.md section 8.4).

See Also:
    - .claude/roadmap.md step 3.12 for the field-by-field description this module implements.
    - .claude/codingrules.md section 8.10 for the Forage map's role in routing and allocation.
    - waggle.messages.forage.values for SourceRef and the shared bounds this module imports.
    - hivemind.forage.map for ForageMap, the owner of a collection of ModelSource.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from waggle.messages.base import CellIdField, UtcDatetime
from waggle.messages.forage import SourceRef
from waggle.messages.forage.values import (
    MAX_MODEL_CHARS,
    MAX_MODEL_GRADE,
    MAX_PROVIDER_CHARS,
    MAX_SOURCE_ID_CHARS,
    MIN_MODEL_GRADE,
)

MAX_CAPABILITIES = (
    16  # A source's capability flags (vision, tool_calls, ...); a handful in practice.
)
MAX_CAPABILITY_CHARS = 32  # One capability flag name, short by convention.

__all__ = [
    "MAX_CAPABILITIES",
    "MAX_CAPABILITY_CHARS",
    "Abundance",
    "Distance",
    "ModelCost",
    "ModelSource",
    "ModelSourceSpec",
]

# A frozen, extras-forbidding config every model in this module shares, matching every other
# boundary value in the Hive (codingrules section 8.5).
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")

# A model grade, 1 (weakest) to 5 (strongest): the same closed range the wire form fixes, imported
# rather than repeated so a manifest entry and a grant can never disagree on what "grade 5" means.
_Grade = Annotated[int, Field(ge=MIN_MODEL_GRADE, le=MAX_MODEL_GRADE)]


class ModelCost(BaseModel):
    """What one source charges: per-token rates, or a per-seat-hour rate for an occupied seat.

    Every rate defaults to 0.0, so a manifest entry that leaves a field unset costs nothing rather
    than failing validation -- the common case for a local, self-hosted model.
    """

    model_config = _MODEL_CONFIG

    cost_per_million_input_usd: float = Field(
        default=0.0,
        ge=0,
        description="US dollars per million input (prompt) tokens; 0.0 for a free or local source.",
    )
    cost_per_million_output_usd: float = Field(
        default=0.0,
        ge=0,
        description="US dollars per million output (generated) tokens; 0.0 for a free or local "
        "source.",
    )
    cost_per_seat_hour_usd: float = Field(
        default=0.0,
        ge=0,
        description="US dollars per seat held for an hour, for a source billed by occupancy "
        "rather than by token; 0.0 when the source has no such charge.",
    )


class ModelSourceSpec(BaseModel):
    """The static half of a Forage map entry: what an operator writes in the Hive Manifest.

    Embedded directly as the model for `[forage.map.<source_id>]` (roadmap step 3.1), so every
    field here is exactly what that manifest section validates.
    """

    model_config = _MODEL_CONFIG

    provider: str = Field(
        max_length=MAX_PROVIDER_CHARS, description="The manifest [llm.providers.*] name."
    )
    model: str = Field(max_length=MAX_MODEL_CHARS, description="The model id this source serves.")
    grade: _Grade = Field(
        description="Hand-set strength from 1 (weakest) to 5 (strongest); later replaced by a "
        "measured score from the Hive's own evaluation runs (codingrules section 8.10)."
    )
    context_window: Annotated[int, Field(gt=0)] = Field(
        description="The model's context window, in tokens. Strictly positive."
    )
    cost: ModelCost = Field(
        default_factory=ModelCost, description="What this source charges, per token or per seat."
    )
    capabilities: tuple[Annotated[str, Field(max_length=MAX_CAPABILITY_CHARS)], ...] = Field(
        default=(),
        max_length=MAX_CAPABILITIES,
        description="Capability flags this source supports (vision, tool_calls, ...).",
    )
    seats: Annotated[int, Field(ge=0)] = Field(
        default=1, description="Concurrent requests this source offers; 1 for most local models."
    )
    host_cell_id: CellIdField | None = Field(
        default=None,
        description="The Cell whose server serves this model; None for a hosted provider.",
    )


class Distance(BaseModel):
    """How far a source is from a given Cell: measured latency and generation speed.

    None on a `ModelSource` until the Fanner (the seat meter every model call passes through,
    `hivemind.llm.fanner`) has measured it at least once (roadmap step 3.12a).
    """

    model_config = _MODEL_CONFIG

    latency_s: Annotated[float, Field(ge=0)] = Field(
        description="Measured round-trip latency to this source from the Cell that measured it."
    )
    tokens_per_s: Annotated[float, Field(ge=0)] = Field(
        description="Measured generation speed from that Cell."
    )
    measured_at: UtcDatetime = Field(description="When these figures were last measured.")


class Abundance(BaseModel):
    """How free a source is right now: seats free, or rate-limit headroom for a hosted provider."""

    model_config = _MODEL_CONFIG

    seats_free: Annotated[int, Field(ge=0)] = Field(
        description="Concurrent requests currently free on this source."
    )
    requests_per_minute_left: Annotated[int, Field(ge=0)] | None = Field(
        default=None,
        description="Requests per minute still available on a hosted provider; None when the "
        "source is not metered that way.",
    )
    tokens_per_minute_left: Annotated[int, Field(ge=0)] | None = Field(
        default=None,
        description="Tokens per minute still available on a hosted provider; None when the "
        "source is not metered that way.",
    )


class ModelSource(BaseModel):
    """One entry on the Forage map: a static spec plus its live distance and abundance.

    `hivemind.forage.map.ForageMap` owns a collection of these under one lock; `observe` and
    `set_abundance` each replace one source's live half with a fresh copy rather than mutating
    this frozen model in place.
    """

    model_config = _MODEL_CONFIG

    source_id: Annotated[str, Field(max_length=MAX_SOURCE_ID_CHARS)] = Field(
        description="The Forage map entry key; unique across the map."
    )
    spec: ModelSourceSpec = Field(description="The static half: what this source is and costs.")
    distance: Distance | None = Field(
        default=None,
        description="Measured latency and speed from a Cell; None before the first measurement.",
    )
    abundance: Abundance = Field(description="Live free-capacity figures.")

    def source_ref(self) -> SourceRef:
        """Build the wire `SourceRef` other Forage models embed when they cross Waggle.

        Returns:
            A `SourceRef` naming this source by id, provider, model and host Cell, with none of
            the live figures a receiver would have to keep re-synchronising (docs/waggle/spec.md
            section 8.4).
        """
        return SourceRef(
            source_id=self.source_id,
            provider=self.spec.provider,
            model=self.spec.model,
            host_cell_id=self.spec.host_cell_id,
        )
