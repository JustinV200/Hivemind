"""Define the episodes resource's read models: a bee's thinking, one episode record at a time.

Thoughts are memory, not audit (codingrules 12): every awake episode and every autopilot decision
leaves an ``EpisodeRecord`` in Bee Bread, and the Observation Hive's thoughts view reads them in
full (codingrules 8.11). A record says who decided, on which slot, what triggered it, the
provider's reasoning summary where one was exposed, the decision and the action, so it may quote
the human: reading one needs ``observe:thoughts`` and ``honey:clearance:c2``. The trigger is
shown by its kind, summary and reference; the untrusted text a trigger may carry (a chat message,
a tool result) stays in memory, since the chat and the task views already show what the human may
read of it. ``EpisodeView`` is also what the episode stream sends.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.models.views``.
    Answered by ``hivemind.entrance.routes.hive.episodes`` and sent by the episode stream;
    published in the OpenAPI document. Calls into memory's episode record and pydantic.

Key invariants:
    - Only answered behind ``observe:thoughts`` and ``honey:clearance:c2``.
    - The assembled prompt is shown by reference only, never as text (it was never stored).

See Also:
    - hivemind.memory.episodes for the record.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import HoneyClearance
from hivemind.forage import ModelSlot
from hivemind.memory import EpisodeRecord

_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Every view here: immutable, no strays.

__all__ = ["EpisodeList", "EpisodeView", "episode_view"]


class EpisodeView(BaseModel):
    """One episode record: who decided what, and why (observe:thoughts, C2)."""

    model_config = _CONFIG

    id: str = Field(description="The episode's id.")
    principal: str = Field(description="Who decided: a bee's id or role.")
    slot: ModelSlot = Field(description="The model slot it ran on.")
    is_autopilot: bool = Field(description="A deterministic autopilot decision, not a model's.")
    trigger_kind: str = Field(description="What kind of thing triggered it.")
    trigger_summary: str = Field(description="A short description of the trigger.")
    trigger_ref: str | None = Field(description="A reference to the trigger's detail, if any.")
    prompt_ref: str | None = Field(description="A reference to the assembled prompt.")
    reasoning_summary: str | None = Field(
        description="The provider's own reasoning summary, where it exposes one."
    )
    decision: str = Field(description="What was decided.")
    action: str = Field(description="What was done about it.")
    clearance: HoneyClearance = Field(description="The record's data-sensitivity label.")
    input_tokens: int | None = Field(description="Tokens the call consumed; null for autopilot.")
    output_tokens: int | None = Field(description="Tokens it generated; null for autopilot.")
    cost_usd: float | None = Field(description="What it cost; null for autopilot.")
    at: datetime = Field(description="When it ran.")


class EpisodeList(BaseModel):
    """Episode records, newest first."""

    model_config = _CONFIG

    episodes: list[EpisodeView] = Field(description="The records, newest first.")


def episode_view(record: EpisodeRecord) -> EpisodeView:
    """Shape an episode record for a device cleared to read thoughts.

    Args:
        record: The record as memory holds it.

    Returns:
        Its view, the trigger's untrusted text left behind.
    """
    usage = record.usage
    return EpisodeView(
        id=record.id,
        principal=record.principal,
        slot=record.slot,
        is_autopilot=record.is_autopilot,
        trigger_kind=record.trigger.kind,
        trigger_summary=record.trigger.summary,
        trigger_ref=record.trigger.payload_ref,
        prompt_ref=record.prompt_ref,
        reasoning_summary=record.reasoning_summary,
        decision=record.decision,
        action=record.action,
        clearance=record.clearance,
        input_tokens=usage.input_tokens if usage is not None else None,
        output_tokens=usage.output_tokens if usage is not None else None,
        cost_usd=usage.cost_usd if usage is not None else None,
        at=record.at,
    )
