"""Run a requested tool through scaffold, quarantine and promotion: the promotion flow.

The Royal Jelly Lab's spine. A request becomes a scaffolded package, the Quarantine Comb (the
sandbox every new tool must pass) proves it, and the Comb Registry (the catalogue of promoted
tools) promotes it at hive or Cell scope; a failing report buys one retry, and every stage writes
a `tool.*` event to the Pheromone Trail. The flow owns the decisions and none of the effects: the
sandbox, Honey and the announcement are ports the composition root fills.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside the royal_jelly package.
    Run by the Queen's tool job (HIVE scope) and by a Warden for its own Cell (CELL scope); calls
    into the scaffold package, the registry, the tool state machine and the trail. The Worker's
    tool invoker reuses its event builders for `tool.loaded` and `tool.load_refused`.

Key invariants:
    - A tool reaches PROMOTED only through `CombRegistry.promote` with a passing report; the flow
      has no other way and no force flag.
    - Every state edge goes through `hivemind.royal_jelly.tool_state.transition`.

See Also:
    - docs/adr/0046 and docs/adr/0047 for the rules the flow follows.
    - .claude/roadmap.md steps 9.6 and 9.8.
    - hivemind.royal_jelly.honey_link for the Nectar a promotion deposits.

Public API:
    - PromotionFlow, PromotionPorts, PromotionOutcome, PathDecider (flow): the flow and its parts.
    - SandboxRunner, HoneyDeposit, Announcer (ports): the seams it calls.
    - ToolIdentity, tool_event and the stage builders (events): minting `tool.*` events.
"""

from hivemind.royal_jelly.promotion.events import (
    SHA256_PREFIX_CHARS,
    ToolIdentity,
    load_refused_event,
    loaded_event,
    promoted_event,
    quarantine_requested_event,
    quarantined_event,
    rejected_event,
    requested_event,
    scaffolded_event,
    tool_event,
)
from hivemind.royal_jelly.promotion.flow import (
    DEFAULT_MAX_RETRIES,
    PathDecider,
    PromotionFlow,
    PromotionOutcome,
    PromotionPorts,
    sandbox_cell_path,
)
from hivemind.royal_jelly.promotion.ports import Announcer, HoneyDeposit, SandboxRunner

__all__ = [
    "DEFAULT_MAX_RETRIES",
    "SHA256_PREFIX_CHARS",
    "Announcer",
    "HoneyDeposit",
    "PathDecider",
    "PromotionFlow",
    "PromotionOutcome",
    "PromotionPorts",
    "SandboxRunner",
    "ToolIdentity",
    "load_refused_event",
    "loaded_event",
    "promoted_event",
    "quarantine_requested_event",
    "quarantined_event",
    "rejected_event",
    "requested_event",
    "sandbox_cell_path",
    "scaffolded_event",
    "tool_event",
]
