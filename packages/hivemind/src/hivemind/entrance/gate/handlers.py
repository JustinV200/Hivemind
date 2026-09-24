"""Answer every refusal with its status and a small JSON body; never with a traceback or an input.

A Landing Board route raises what the flow below it raised; this module turns it into the answer a
client can act on (ADR-0033, ADR-0034). The status comes from the error's category
(``hivemind.common.errors``): not found is 404, a conflict 409, a permission refusal 403, a failed
authentication 401, a rate limit 429, a missed deadline 504, a configuration gap 503; the gate's own
``step_up_required`` answers 403 with its reason and, for a device that cannot step up, the pending
confirmation's id. A request body that does not validate answers 422 naming only the fields,
never their values, so a mistyped password is never echoed into a client's logs. Anything else the
Hive raised on purpose is a 500 with its code and a fixed sentence.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.gate``. Installed on both
    applications by ``hivemind.entrance.app``. Calls into ``hivemind.common.errors`` and the gate's
    own errors.

Key invariants:
    - A body is ``ErrorBody``: a stable code, a sentence, and step-up's reason and pending id.
    - No body carries a request's input, a credential, or an internal error's message.

See Also:
    - hivemind.entrance.gate.errors for the gate's own refusals.
    - docs/entrance/openapi.json for the ``ErrorBody`` schema clients read.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hivemind.common.errors import (
    ConfigurationError,
    ConflictError,
    DeadlineExceededError,
    HiveMindError,
    NotFoundError,
    PermissionDeniedError,
)
from hivemind.common.logging import get_logger
from hivemind.entrance.errors import AuthenticationFailedError
from hivemind.entrance.gate.errors import (
    CapabilityDeniedError,
    RateLimitedError,
    StepUpRequiredError,
)

INVALID_REQUEST_CODE = "hivemind.entrance.invalid_request"  # A body or parameter did not validate.
INTERNAL_CODE_DETAIL = "The Hive could not complete this request."  # A 500's fixed sentence.
_MAX_FIELDS_NAMED = 8  # A 422 names at most this many fields: enough to fix, never a dump.
# Checked in order: the first category an error belongs to decides its status.
_STATUSES: tuple[tuple[type[HiveMindError], int], ...] = (
    (AuthenticationFailedError, 401),
    (RateLimitedError, 429),
    (NotFoundError, 404),
    (ConflictError, 409),
    (PermissionDeniedError, 403),
    (DeadlineExceededError, 504),
    (ConfigurationError, 503),
)

log = get_logger(__name__)

__all__ = ["INVALID_REQUEST_CODE", "ErrorBody", "install_error_handlers", "status_for"]


class ErrorBody(BaseModel):
    """The body of every refusal the Landing Board answers with."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    error: str = Field(description="A stable error code, e.g. hivemind.entrance.step_up_required.")
    detail: str = Field(description="One sentence naming what was refused; never a credential.")
    reason: str | None = Field(
        default=None,
        description="For step_up_required: why (over_step_up_spend, over_daily_cap, "
        "sensitive_action, new_network).",
    )
    pending_id: str | None = Field(
        default=None,
        description="For step_up_required from a device that cannot step up: the pending "
        "confirmation a person confirms from an interactive device.",
    )
    capability: str | None = Field(
        default=None, description="For capability_denied: the capability the device lacks."
    )


def install_error_handlers(app: FastAPI) -> None:
    """Install the Landing Board's error handlers on ``app``.

    Args:
        app: A freshly built Entrance application.
    """
    app.add_exception_handler(HiveMindError, _hive_error)
    app.add_exception_handler(RequestValidationError, _invalid_request)
    app.add_exception_handler(ValidationError, _invalid_request)


def status_for(error: HiveMindError) -> int:
    """Return the HTTP status an error answers with, from its category.

    Args:
        error: What a route raised.

    Returns:
        The status; 500 for an error in none of the categories.
    """
    return next((status for kind, status in _STATUSES if isinstance(error, kind)), 500)


async def _hive_error(request: Request, error: Exception) -> JSONResponse:
    """Answer a HiveMindError with its category's status and a body carrying its code."""
    # Starlette only routes HiveMindErrors here; the check narrows the type for the code below.
    if not isinstance(error, HiveMindError):
        fallback = ErrorBody(error=HiveMindError.code, detail=INTERNAL_CODE_DETAIL)
        return JSONResponse(status_code=500, content=fallback.model_dump(mode="json"))
    status = status_for(error)
    body = ErrorBody(error=error.code, detail=str(error))
    if isinstance(error, StepUpRequiredError):
        body = body.model_copy(
            update={"reason": error.reason.value, "pending_id": error.pending_id}
        )
    elif isinstance(error, CapabilityDeniedError):
        body = body.model_copy(update={"capability": error.capability})
    elif status == 500:
        # An invariant the Hive broke: its message stays in the log, never in a client's hands.
        log.error("entrance.route_failed", path=request.url.path, code=error.code)
        body = ErrorBody(error=error.code, detail=INTERNAL_CODE_DETAIL)
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"))


async def _invalid_request(request: Request, error: Exception) -> JSONResponse:
    """Answer a body or parameter that did not validate: 422, naming fields, never values."""
    issues = error.errors() if isinstance(error, RequestValidationError | ValidationError) else []
    fields = [".".join(str(part) for part in issue.get("loc", ())) for issue in issues]
    named = ", ".join(field for field in fields[:_MAX_FIELDS_NAMED] if field) or "the body"
    body = ErrorBody(error=INVALID_REQUEST_CODE, detail=f"These fields are not valid: {named}.")
    return JSONResponse(status_code=422, content=body.model_dump(mode="json"))
