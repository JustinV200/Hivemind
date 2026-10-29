"""Define the help Ask's refusals: no Ask on this Hive, and a session past its question budget.

The help's Ask (ADR-0069, ``GET /v1/help/ask``) refuses for two reasons of its own: the Hive was
served without a front-end build, so there are no help pages to answer from (the route is not
even mounted then, and the error is the belt to that brace), or a session has asked more
questions this minute than its budget, each one a model call the Hive pays for. Each is an
``EntranceError`` with a stable code, answered as the ``ErrorBody`` of its category's status.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.help``. Raised by the
    help route and door. Calls into ``hivemind.entrance.errors``, ``hivemind.common.errors`` and
    the gate's ``RateLimitedError`` only.

Key invariants:
    - Every class sets its own code; no message carries the question.

See Also:
    - hivemind.entrance.gate.handlers for how a refusal becomes a status and a body.
"""

from __future__ import annotations

from typing import ClassVar

from hivemind.common.errors import NotFoundError
from hivemind.entrance.errors import EntranceError
from hivemind.entrance.gate.errors import RateLimitedError

__all__ = ["HelpNotServedError", "QuestionOverBudgetError"]


class HelpNotServedError(EntranceError, NotFoundError):
    """Raise when the Hive has no help pages to answer from (no front-end build was served)."""

    code: ClassVar[str] = "hivemind.entrance.help_not_served"

    def __init__(self) -> None:
        """Build the error; one fixed message, since the cause is the deployment's."""
        super().__init__("This Hive serves no help pages, so there is nothing to answer from.")


class QuestionOverBudgetError(RateLimitedError):
    """Raise when a session has asked its questions for the minute."""

    code: ClassVar[str] = "hivemind.entrance.help_over_budget"

    def __init__(self) -> None:
        """Build the error; one fixed message, since the budget is fixed."""
        # Past RateLimitedError's own sentence, which speaks of requests, not questions.
        super(RateLimitedError, self).__init__(
            "This session has asked its help questions for the minute; ask again shortly."
        )
