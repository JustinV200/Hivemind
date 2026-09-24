"""Name every way a Landing Board conversation fails, each as one sentence an operator can act on.

The Landing Board (the Hive Entrance's HTTP contract) answers every refusal with the same body,
``hivemind.entrance.gate.ErrorBody``: a stable code, one sentence, and for a step-up the reason
and, when the device cannot step up itself, the id of the pending confirmation holding its
request (ADR-0033). ``LandingRefusedError`` carries that body to the CLI's commands, and its message
adds what the code means for the person at the terminal: a failed login never says which factor
failed (so the message lists what it may be: a wrong password, a device still pending approval,
locked or revoked), and a held request names the confirmation a person must give.
``EntranceUnreachableError`` is the other failure: nothing answered, or TLS did not verify.
``LandingProtocolError`` is an answer the CLI could not read. None of them ever carries a token,
a password, a key or a signature: the Entrance never puts one in a refusal, and the CLI adds none.

Fits into the Hive:
    Layer 7 (the terminal), inside ``hivemind.cli.landing``. Raised by
    ``hivemind.cli.landing.client`` and ``.stream``; caught and printed by every command that
    talks to the Landing Board. Calls into ``hivemind.common.errors`` and the Entrance's
    ``ErrorBody``.

Key invariants:
    - Every error's message is one line, and names no credential.
    - ``LandingRefusedError.wants_step_up`` is true only for an interactive device's own step-up.

See Also:
    - hivemind.entrance.gate.handlers for the refusal body and each status.
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md, "Step-up needs a
      human".
"""

from __future__ import annotations

from typing import ClassVar

import httpx
from pydantic import ValidationError

from hivemind.common.errors import HiveMindError, PermissionDeniedError
from hivemind.entrance.gate import NOT_FOUND_CODE, ErrorBody

STEP_UP_CODE = "hivemind.entrance.step_up_required"  # ADR-0033's 403 step_up_required.
AUTHENTICATION_CODE = "hivemind.entrance.authentication_failed"  # A refused login or session.
CAPABILITY_CODE = "hivemind.entrance.capability_denied"  # The device lacks the route's capability.
RATE_CODE = "hivemind.entrance.rate_limited"  # Past the per-device or per-address rate.
# What a refused login or session may mean: the Entrance never says which (ADR-0033).
AUTHENTICATION_HINT = (
    "the password may be wrong, or this device may be pending approval, locked or revoked "
    "(the Entrance never says which)"
)
NOT_FOUND_HINT = "a loopback-only route does not exist on the remote listener"
_MAX_DETAIL_CHARS = 300  # A refusal's sentence as printed; the Entrance's own are far shorter.

__all__ = [
    "AUTHENTICATION_CODE",
    "CAPABILITY_CODE",
    "RATE_CODE",
    "STEP_UP_CODE",
    "EntranceUnreachableError",
    "LandingError",
    "LandingProtocolError",
    "LandingRefusedError",
]


class LandingError(HiveMindError):
    """Root of every failure talking to the Landing Board from the CLI."""

    code: ClassVar[str] = "hivemind.cli.landing.error"


class EntranceUnreachableError(LandingError):
    """Raise when no Entrance answered: refused, timed out, or its TLS did not verify."""

    code: ClassVar[str] = "hivemind.cli.landing.unreachable"


class LandingProtocolError(LandingError):
    """Raise when the Entrance answered something the CLI could not read as the contract says."""

    code: ClassVar[str] = "hivemind.cli.landing.protocol"


class LandingRefusedError(LandingError, PermissionDeniedError):
    """Raise when the Landing Board refused a request; carries its body.

    Attributes:
        status: The HTTP status.
        body: The refusal as the Entrance sent it.
    """

    code: ClassVar[str] = "hivemind.cli.landing.refused"

    def __init__(self, status: int, body: ErrorBody) -> None:
        """Build the error from one refusal.

        Args:
            status: The HTTP status.
            body: The Entrance's refusal body.
        """
        super().__init__(_explain(status, body))
        self.status = status
        self.body = body

    @property
    def wants_step_up(self) -> bool:
        """Whether an interactive device should step up and ask again.

        Returns:
            True for ``step_up_required`` with no pending confirmation; a device that cannot
            step up gets a confirmation id instead, and asking again would not help.
        """
        return self.body.error == STEP_UP_CODE and self.body.pending_id is None

    @property
    def pending_id(self) -> str | None:
        """The pending confirmation holding the request, for a device that cannot step up."""
        return self.body.pending_id

    @classmethod
    def from_response(cls, response: httpx.Response) -> LandingRefusedError:
        """Read a refusal from a response whose status is 4xx or 5xx.

        Args:
            response: The Entrance's answer.

        Returns:
            The refusal; a body that is not an ``ErrorBody`` (a proxy's page, say) becomes a
            generic one naming only the status.
        """
        try:
            body = ErrorBody.model_validate_json(response.content)
        except ValidationError:
            # Not the Entrance's own refusal shape: say only what the status says.
            detail = f"The Entrance answered HTTP {response.status_code}."
            body = ErrorBody(error="http", detail=detail)
        return cls(response.status_code, body)


def _explain(status: int, body: ErrorBody) -> str:
    """Return one line: the Entrance's sentence, its code, and what that means here."""
    detail = body.detail.replace("\n", " ")[:_MAX_DETAIL_CHARS]
    hint = _hint(status, body)
    tail = f" ({hint})" if hint else ""
    return f"{detail} [{body.error}]{tail}"


def _hint(status: int, body: ErrorBody) -> str | None:
    """Say what a refusal means for the person at the terminal, when its sentence does not."""
    # A held request: nothing this device can do, but a person can confirm it elsewhere.
    if body.error == STEP_UP_CODE and body.pending_id is not None:
        return (
            f"held as pending confirmation {body.pending_id}: a person confirms it from an "
            "interactive device"
        )
    if body.error == AUTHENTICATION_CODE:
        return AUTHENTICATION_HINT
    if body.error == CAPABILITY_CODE and body.capability is not None:
        return f"ask the operator to grant {body.capability} with hive entrance approve or steward"
    # A route absent from this listener (a loopback-only one on the remote listener among them)
    # answers the router's own not-found code; an Entrance before that code answered none.
    if status == 404 and (body.error == NOT_FOUND_CODE or not body.error.startswith("hivemind.")):
        return NOT_FOUND_HINT
    return None
