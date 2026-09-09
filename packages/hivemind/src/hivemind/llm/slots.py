"""Define BoundModel: one ModelSlot resolved to a live provider, a model id, and a fallback chain.

A `hivemind.forage.slots.ModelSlot` (`QUEEN`, `WORKER`, ...) names *where* a call fits in the
Hive; a `BoundModel` says *how* to actually make that call right now: which `LLMProvider`
instance to use, which of its model ids, at what `Effort`, inside what context window, and what
to try next if this binding fails. `binding` is the manifest key that produced this value -- the
slot's own lowercase name (`"worker"`) or a named binding referenced only from a fallback chain
(`"local_worker"`), per the phase 3 brief's `[llm.slots]` shape -- so a Pheromone Trail event or a
log line can say which manifest row is in play without re-deriving it from the provider and model
id. `cost_per_million_input_usd`/`cost_per_million_output_usd` are a copy of the Forage map's
prices taken at bind time, not a live reference: `hivemind.forage`'s own `ModelCost` type is
still being written by a parallel dispatch this phase, and `hivemind.llm` may not import a module
that does not yet exist (and must not import it later either, since codingrules section 4 fixes
`llm -> forage` as the only direction and `forage` must stay free to change its price shape
without `llm` needing to follow). Two plain floats bought against that constraint are enough for
every caller here: routing compares a call's estimated cost to a budget, and the Fanner (a later
roadmap step) meters spend against these same two numbers.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Built by `hivemind.llm.slots.resolve`
    (roadmap step 3.4, a later dispatch: not implemented in this module yet) from a
    `hivemind.manifest.HiveManifest`'s `[llm.slots]` table and the provider registry; read by
    every Worker, Warden and the Queen wherever codingrules section 8.6 requires a `ModelSlot`
    rather than a model name. Calls into `hivemind.forage.slots` (for `ModelSlot` and `Effort`)
    and `hivemind.llm.provider` only.

Key invariants:
    - Frozen and slotted (codingrules section 8.5: "internal values are
      @dataclass(frozen=True, slots=True)"); this is not a boundary model, so it is a dataclass,
      not a pydantic BaseModel -- nothing here is read from or written to JSON/TOML directly.
    - `fallback` is `None` at the end of a chain; a caller walks it by following `.fallback`
      until `None`, never by re-resolving the manifest mid-call.
    - `cost_per_million_input_usd`/`cost_per_million_output_usd` are `None` exactly when the
      Forage map (or a manifest override) lists no price for this binding's model; `None` means
      "unpriced", never "free" (mirrors `hivemind.llm.models.Usage.cost_usd`'s same convention).

See Also:
    - .claude/codingrules.md section 8.6 for "model slots, not model names" and the fallback rule.
    - .claude/codingrules.md section 4 for why `llm` may depend on `forage` but never the reverse.
    - docs/adr/0008-llm-provider-independence-and-model-slots.md for the decision this implements.
    - hivemind.forage.slots for ModelSlot and Effort, the two enums this dataclass carries.
    - hivemind.llm.provider for LLMProvider, the protocol `provider` is bound to.

NOTE(A2): `resolve(slot: ModelSlot, manifest: HiveManifest) -> BoundModel` is roadmap step 3.4,
    owned by dispatch C1 (`llm/slots.py` gains it, plus `llm/registry.py`, in a later wave). This
    module intentionally stops at the shape `resolve` will return.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.forage.slots import Effort, ModelSlot
from hivemind.llm.provider import LLMProvider

__all__ = ["BoundModel"]


@dataclass(frozen=True, slots=True)
class BoundModel:
    """One ModelSlot resolved to a live provider, a model id, its effort, and a fallback chain.

    See the module docstring for why cost is carried as two plain floats rather than a
    `hivemind.forage` cost type.
    """

    slot: ModelSlot  # The slot this binding serves.
    binding: str  # The manifest [llm.slots] key that produced this: the slot's own key or a name.
    provider: LLMProvider  # The live provider instance to call.
    model: str  # The provider's own model id.
    effort: Effort  # How hard to ask the model to think on this binding.
    context_window: int  # This binding's context window, in tokens (copied at bind time).
    cost_per_million_input_usd: float | None  # Forage map price, or None when unpriced.
    cost_per_million_output_usd: float | None  # Forage map price, or None when unpriced.
    fallback: BoundModel | None = None  # Next binding to try on failure; None ends the chain.
