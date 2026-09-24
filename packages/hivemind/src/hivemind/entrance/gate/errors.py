"""Define the refusals only the Entrance's gate makes: step-up owed, capability denied, too fast.

Most refusals a Landing Board route answers with come from the flows below it (enrolment, login,
the Queen's own bookkeeping) and are mapped to a status by their ``hivemind.common.errors``
category. Three are the gate's own: ``StepUpRequiredError`` (ADR-0033's ``403 step_up_required``,
carrying the reason and, for a device that cannot step up, the pending confirmation a person must
confirm), ``CapabilityDeniedError`` (the Guard refused the route at the Entrance route enforcement
point) and ``RateLimitedError`` (a device or an address is over its rate).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.gate``. Raised by the
    gate's dependency and by routes that guard a sensitive action; answered by the gate's error
    handlers. Calls into ``hivemind.entrance.errors`` only.

Key invariants:
    - Every error has a stable ``code``; none carries a credential or a request's content.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md, "Step-up needs a
      human".
    - hivemind.entrance.gate.handlers for the statuses they are answered with.
"""

from __future__ import annotations

from typing import ClassVar

from hivemind.common.errors import PermissionDeniedError
from hivemind.entrance.auth.step_up.rules import StepUpReason
from hivemind.entrance.errors import EntranceError

__all__ = ["CapabilityDeniedError", "RateLimitedError", "StepUpRequiredError"]


class StepUpRequiredError(EntranceError, PermissionDeniedError):
    """Raise when a request needs a step-up its session does not have (``403 step_up_required``).

    An interactive device steps up and asks again; a device that cannot step up gets the id of a
    pending confirmation instead, which a person confirms from an interactive device.
    """

    code: ClassVar[str] = "hivemind.entrance.step_up_required"

    def __init__(self, reason: StepUpReason, pending_id: str | None = None) -> None:
        """Build the error.

        Args:
            reason: Why a step-up is needed.
            pending_id: The pending confirmation holding the request, for a device that cannot
                step up; None for an interactive device.
        """
        tail = f"; a person confirms {pending_id}" if pending_id is not None else ""
        super().__init__(f"This request needs a step-up ({reason.value}){tail}.")
        self.reason = reason
        self.pending_id = pending_id


class CapabilityDeniedError(EntranceError, PermissionDeniedError):
    """Raise when the Guard refuses a device a route at the Entrance route enforcement point."""

    code: ClassVar[str] = "hivemind.entrance.capability_denied"

    def __init__(self, capability: str) -> None:
        """Build the error.

        Args:
            capability: The capability the device does not hold.
        """
        super().__init__(f"This device does not hold {capability}.")
        self.capability = capability


class RateLimitedError(EntranceError, PermissionDeniedError):
    """Raise when a device is over its request rate (``rate_limit_per_device``)."""

    code: ClassVar[str] = "hivemind.entrance.rate_limited"

    def __init__(self) -> None:
        """Build the error; one fixed message, since the limit is the manifest's."""
        super().__init__("Too many requests; slow down and try again shortly.")
