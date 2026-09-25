"""Define the facts a tier floor reads beyond a Cell's tier: a control link and a goal request.

Two Night Veil floors (roadmap steps 10.3a and 10.3c, ADR-0039) need facts that neither the held
set nor a Cell's tier can express, so the enforcement point that knows them states them on the
request's `PolicyContext`. `ControlLink` is the Waggle control channel a Cell about to be
provisioned will dial the Queen through: the host its client dials and the SOCKS proxy it dials
through; the control-link floor refuses a Night Veil Cell any link that is not a `.onion` host
reached through a Tor SOCKS proxy on the Cell's own loopback. `GoalRequestFacts` is what the
durable goal request a task was planned from says, as its row in the Queen's goal-request store
holds it: who asked, and which tier they asked for; the initiation floor refuses Night Veil
placement unless a human's request named that tier explicitly. Both are frozen boundary values,
because a request is carried whole into the Guard's decision.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.guard.policy`. Built
    by the Queen's placement and provisioning points (`hivemind.queen.dispatcher`); read by
    `hivemind.guard.policy.floors`. Calls into `hivemind.cell` (the tier enum and RequestOrigin).

Key invariants:
    - Both models are frozen and forbid extra fields: a fact is stated once and never edited.
    - A `ControlLink` states what the endpoint IS, never what it should be: the floor decides.

See Also:
    - docs/adr/0039-capability-model-attenuation-and-enforcement-points.md, "Floors hold whatever
      a set says".
    - docs/adr/0030-night-veil-retention-and-clearance-boundary.md for the hidden-service link.
    - hivemind.guard.policy.models for PolicyContext, which carries both.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import CombShieldLevel, RequestOrigin

_MAX_HOST_CHARS = 253  # RFC 1035's limit on a DNS name, which bounds any host a link names.
_MAX_URL_CHARS = 2_048  # A proxy URL is short; this only keeps a malformed one from growing.

__all__ = ["ControlLink", "GoalRequestFacts"]


class ControlLink(BaseModel):
    """The Waggle control channel a Cell will dial the Queen through: its host and its proxy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    host: str = Field(
        min_length=1,
        max_length=_MAX_HOST_CHARS,
        description="The host the Cell's Waggle client dials: a .onion hidden service for a "
        "Night Veil Cell, the Hive Stand's reachable address for any other.",
    )
    socks_proxy_url: str | None = Field(
        default=None,
        max_length=_MAX_URL_CHARS,
        description="The SOCKS proxy the client dials through; None when it dials directly.",
    )


class GoalRequestFacts(BaseModel):
    """What the durable goal request a task was planned from says, as its stored row holds it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    origin: RequestOrigin = Field(description="Who asked for the goal, per the request row.")
    comb_shield: CombShieldLevel | None = Field(
        default=None,
        description="The Comb Shield tier the request asked for explicitly; None when it named "
        "no tier and left tiers to the planner.",
    )
