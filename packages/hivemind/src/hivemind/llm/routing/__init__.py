"""Choose which binding of a slot's fallback chain a bee calls first: llm.routing.

Routing is a pure function from one ``RoutingInputs`` (the slot, the task's tempo, the
capabilities it needs, the Forage map's sources, the bee's Cell and that Cell's hosting plan, the
``[llm.slots]`` rows and the operator's ``RoutingRules``) to one ``RoutingDecision``. It never
invents a binding: it reorders the slot's own manifest fallback chain, chosen first, picks the
effort from tempo (how fast a task must be done and how right it must be), and reports why as
data, what it passed over, and a Forage shortfall when the chosen source has no free seat. The
Warden and the Queen wire it into spawn and rebind in later roadmap steps; the Fanner (the seat
meter every model call passes through) keeps spilling along the reordered chain at call time.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Imports ``hivemind.forage`` and
    ``hivemind.llm.slots``; imported by the Warden, the Queen and the composition root
    (``hivemind.cli.stores.routing_rules``) through ``hivemind.llm``.

Key invariants:
    - ``choose_binding`` is pure: no I/O, no clock, no lock; same inputs, same decision.
    - The chosen key is always in the slot's chain, and ``order`` is a permutation of it.
    - Tempo raises a grade floor and never lowers it.
    - Routing never refuses: with nothing eligible it chooses the slot's own primary.

See Also:
    - .claude/roadmap.md step 8.1 and .claude/codingrules.md sections 8.6, 8.10 and 8.14.
    - hivemind.llm.fanner.spill for the call-time spill rule.
    - hivemind.queen.forage.hosting for the hosting plan routing ranks by.

Public API:
    - choose_binding: the pure routing function.
    - RoutingInputs, RoutingRules, default_floors: what it reads.
    - RoutingDecision, RoutingReason, PassedOver, PassOverCause, ForageShortfall: what it returns.
    - choose_effort, TIGHT_BUDGET_S: the tempo-to-effort rule.
    - cheapest_chat_binding, CHAT_SLOTS: the chat row the map prices lowest (the help's Ask).
"""

from hivemind.llm.routing.cheapest import CHAT_SLOTS, cheapest_chat_binding
from hivemind.llm.routing.decision import (
    ForageShortfall,
    PassedOver,
    PassOverCause,
    RoutingDecision,
    RoutingReason,
)
from hivemind.llm.routing.effort import TIGHT_BUDGET_S, choose_effort
from hivemind.llm.routing.inputs import RoutingInputs
from hivemind.llm.routing.policy import choose_binding
from hivemind.llm.routing.rules import RoutingRules, default_floors

__all__ = [
    "CHAT_SLOTS",
    "TIGHT_BUDGET_S",
    "ForageShortfall",
    "PassOverCause",
    "PassedOver",
    "RoutingDecision",
    "RoutingInputs",
    "RoutingReason",
    "RoutingRules",
    "cheapest_chat_binding",
    "choose_binding",
    "choose_effort",
    "default_floors",
]
