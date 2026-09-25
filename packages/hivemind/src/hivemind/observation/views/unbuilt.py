"""Define the answer of a resource the contract names before the phase that fills it lands.

ADR-0040: a Landing Board resource whose subsystem is not built yet (``tools`` before phase 9,
``honey`` before phase 7, ``swarm`` before phase 11) answers ``501 Not Implemented`` naming the
phase that fills it, so the contract names every resource from the start and a client can be
written against the whole of it. ``NotBuiltView`` is that answer; it shares ``error`` and
``detail`` with every other refusal's body, so a client's error handling reads it unchanged.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.observation.views``.
    Answered by ``hivemind.entrance.routes.later``; published in the OpenAPI document. Calls
    into pydantic only.

Key invariants:
    - ``error`` is always ``NOT_BUILT_CODE``; ``phase`` is the roadmap phase that fills it.

See Also:
    - docs/adr/0040-hive-entrance-http-websocket-api-and-human-inbox.md, "Routes, one module per
      resource".
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

NOT_BUILT_CODE: Final = "hivemind.entrance.not_built"  # The stable code every 501 answer carries.

_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Every view here: immutable, no strays.

__all__ = ["NOT_BUILT_CODE", "NotBuiltView"]


class NotBuiltView(BaseModel):
    """A resource the contract names whose phase has not landed yet (501)."""

    model_config = _CONFIG

    error: Literal["hivemind.entrance.not_built"] = Field(
        default=NOT_BUILT_CODE, description="Always hivemind.entrance.not_built."
    )
    detail: str = Field(description="One sentence naming the resource and the phase.")
    resource: str = Field(description="The resource: tools, honey or swarm.")
    phase: int = Field(description="The roadmap phase that fills it.")
