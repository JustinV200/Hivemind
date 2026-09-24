"""Serve the llm resource: the model providers, how the Queen judges them, and the slot bindings.

Every model call goes through a slot bound to a provider and a model (codingrules 8.6). ``GET
/v1/llm`` shows that wiring and the Queen's own judgement of each provider, from her health poller
and Clustering state, read directly (ADR-0032) and never by probing a provider. It needs
``observe``. A provider appears by its manifest name and kind: never an API key, the variable that
holds one, or a base URL.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes.hive``.
    Registered in the route table. Calls into ``hivemind.entrance.reads`` (``llm_view``).

Key invariants:
    - Read only; no secret in the answer, and no provider called to answer it.

See Also:
    - hivemind.entrance.reads.llm for the judgement.
"""

from __future__ import annotations

from hivemind.entrance.gate.params import Services
from hivemind.entrance.gate.spec import BOTH_LISTENERS, RouteEffect, RouteSpec, session_with
from hivemind.entrance.reads import llm_view
from hivemind.observation import LlmView

OBSERVE = "observe"  # The LLM wiring is a read-only view.

__all__ = ["ROUTES"]


async def read_llm(services: Services) -> LlmView:
    """Read the providers, their health and the slot bindings.

    Args:
        services: The Entrance's services (the LLM reads).

    Returns:
        The Queen's mode, every provider and every binding.
    """
    return llm_view(services.hive.llm)


ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec(
        method="GET",
        path="/v1/llm",
        listeners=BOTH_LISTENERS,
        access=session_with(OBSERVE),
        effect=RouteEffect.READ,
        endpoint=read_llm,
        summary="Read the model providers, their health and the slot bindings (no keys).",
        response_model=LlmView,
    ),
)
