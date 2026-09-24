"""Define the llm resource's read model: the providers, their health, and the slot bindings.

Every model call in the Hive goes through a slot (codingrules 8.6), and a slot is bound in the
manifest to a provider and a model, with a fallback chain. ``LlmView`` shows the operator that
wiring and how the Queen currently judges each provider: HEALTHY while her health poller has seen
no failure, DEGRADED once it has read DOWN at least once in a row, and DOWN once Clustering paused
the bees bound to it (roadmap 4.9). The judgement is the Queen's own bookkeeping: answering this
read never probes a provider. A provider is shown by its manifest name and kind only: no base
URL (it may carry credentials) and never an API key or the variable that holds one.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.models.views``. Built
    by ``hivemind.entrance.reads.llm``; answered by ``hivemind.entrance.routes.hive.llm``;
    published in the OpenAPI document. Calls into the forage slot enums, the Queen's mode and
    pydantic.

Key invariants:
    - No API key, key variable or provider URL is ever a field.

See Also:
    - hivemind.queen.cluster.health for the poller the health is read from.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from hivemind.forage import Effort, ModelSlot
from hivemind.queen import QueenMode

_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Every view here: immutable, no strays.

__all__ = ["LlmView", "ProviderHealthView", "ProviderView", "SlotView"]


class ProviderHealthView(Enum):
    """How the Queen currently judges a provider, from her own bookkeeping."""

    HEALTHY = "HEALTHY"  # No failed probe on record.
    DEGRADED = "DEGRADED"  # Her health poller read it DOWN at least once in a row.
    DOWN = "DOWN"  # Clustering paused the bees bound to it: down with no fallback.


class ProviderView(BaseModel):
    """One configured model provider."""

    model_config = _CONFIG

    name: str = Field(description="Its [llm.providers.<name>] key.")
    kind: str = Field(description="Its adapter kind (anthropic, openai_compat, fake, ...).")
    health: ProviderHealthView = Field(description="How the Queen judges it now.")
    clustered: bool = Field(description="Clustering has paused the bees bound to it.")
    failed_probes: int = Field(description="Consecutive DOWN readings her poller has seen.")


class SlotView(BaseModel):
    """One [llm.slots] binding: a slot, or a named binding a fallback chain reaches."""

    model_config = _CONFIG

    key: str = Field(description="The [llm.slots] key.")
    serves: ModelSlot | None = Field(
        description="The slot it serves: its own, or the slot whose fallback chain reaches it."
    )
    provider: str = Field(description="The provider it binds to.")
    model: str = Field(description="The model id it binds to, as the manifest names it.")
    fallback: str | None = Field(description="The binding it falls back to, if any.")
    effort: Effort = Field(description="How hard the model is asked to think.")


class LlmView(BaseModel):
    """The LLM wiring and how the Queen judges it (observe)."""

    model_config = _CONFIG

    queen_mode: QueenMode = Field(description="The Queen's own mode: RUNNING or CLUSTERED ...")
    providers: list[ProviderView] = Field(description="Every configured provider, manifest order.")
    slots: list[SlotView] = Field(description="Every [llm.slots] binding, manifest order.")
