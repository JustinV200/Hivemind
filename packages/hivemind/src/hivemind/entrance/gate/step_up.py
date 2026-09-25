"""Guard a sensitive action with step-up: pass it, refuse it, or hold it for a person.

ADR-0041's rules (``requires_step_up``) say when a request needs a step-up the session does not
have: a goal above ``step_up_spend`` or past the device's daily cap, a key or capability change,
reopening, locking another device. ``require_step_up`` applies them for a route: an interactive
device that has not stepped up gets ``403 step_up_required`` and steps up; a device no person types
at cannot, so when the route can carry the action out later it is held as a pending confirmation
(``hold``, which also tells every other device) and the refusal names it, and when it cannot the
refusal stands alone. Nothing a program asks for is ever let through on its own two factors.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.gate``. Called by the
    routes that guard a sensitive action (goals, devices, the Entrance's own door). Calls into the
    step-up rules and the confirmation flow.

Key invariants:
    - A stepped-up interactive session passes; nothing else does when the rules say step-up.
    - Only a non-interactive device's request is held, and only with the payload the route needs
      to carry it out; the payload stays in the pending table.

See Also:
    - hivemind.entrance.auth.step_up.rules for the rules.
    - hivemind.entrance.routes.entrance for where a held request is confirmed and carried out.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import JsonValue

from hivemind.entrance.auth.confirm import hold
from hivemind.entrance.auth.step_up import ActionKind, GoalSpend, requires_step_up
from hivemind.entrance.gate.admit import Caller
from hivemind.entrance.gate.errors import StepUpRequiredError
from hivemind.entrance.gate.services import EntranceServices

__all__ = ["require_step_up"]


async def require_step_up(
    services: EntranceServices,
    caller: Caller,
    action: ActionKind,
    spend: GoalSpend | None = None,
    held: Mapping[str, JsonValue] | None = None,
) -> None:
    """Let ``action`` through, or refuse it pending a step-up (and a person, for a program).

    Args:
        services: The Entrance's services (the rules, the enrolment dependencies).
        caller: The admitted caller asking.
        action: What it asks for.
        spend: A goal's budget and the device's spend today; required for a goal.
        held: What the route needs to carry the action out later, when a person confirms it
            for a device that cannot step up; None when the action cannot be held.

    Raises:
        StepUpRequiredError: A step-up is needed; for a held request, it names the pending
            confirmation a person must confirm.
    """
    reason = requires_step_up(
        caller.session, action, spend, step_up_spend=services.rules.step_up_spend
    )
    if reason is None:
        return
    # An interactive device steps up itself and asks again; so does one whose action cannot wait.
    if caller.session.interactive or held is None:
        raise StepUpRequiredError(reason)
    # Latency: one local transaction (the hold and its event), then a queued notice.
    pending_id = await hold(
        services.enrolment, caller.device, action, held, services.rules.confirmation_ttl
    )
    raise StepUpRequiredError(reason, pending_id)
