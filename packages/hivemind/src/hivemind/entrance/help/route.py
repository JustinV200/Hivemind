"""Serve ``GET /v1/help/ask``: one question about the Hive, answered from its help pages.

The Observation Hive's Help screen has an Ask (ADR-0069): a person types a question and the Hive
answers it from the help pages it serves, citing the sections it used, on the cheapest chat
binding the manifest configures. It is a read, as the Honey search is (``GET /v1/honey/search``
also costs a model call): nothing in the Hive changes, no store is written, the question is
never kept. Any approved device's session may ask, since the pages are the same ones every
session can open on the Help screen; each session is held to a few questions a minute on top of
its device's request rate. The row is mounted only while the Entrance was built with help pages
(``Switch.HELP``): a Hive served without a front-end build answers 404 here, as it has no Help
screen either.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.help``. Registered in
    the route table by ``hivemind.entrance.routes.registry``. Calls into the help door.

Key invariants:
    - A GET with ``RouteEffect.READ``: the Observation Hive's write guard needs no new pattern.
    - The question reaches the door only through the bounded query model.

See Also:
    - hivemind.entrance.help.door for what answering does.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Query

from hivemind.entrance.gate.params import CallerParam, Services
from hivemind.entrance.gate.services import EntranceServices
from hivemind.entrance.gate.spec import BOTH_LISTENERS, RouteEffect, RouteSpec, Switch, session_with
from hivemind.entrance.help.door import ask
from hivemind.entrance.help.errors import HelpNotServedError
from hivemind.entrance.help.models import HelpAnswerView, HelpAskQuery
from hivemind.entrance.help.services import HelpServices

HELP_PATH = "/v1/help/ask"  # The help's one route.
TOO_MANY_REQUESTS = 429  # A session past its question budget.
# Beyond the refusals every row declares: what only a question can be answered with.
HELP_REFUSALS: tuple[tuple[int, str], ...] = (
    (TOO_MANY_REQUESTS, "The session has asked its help questions for the minute."),
)

__all__ = ["HELP_PATH", "HELP_REFUSALS", "HELP_ROUTES", "ask_help", "help_of"]


def help_of(services: EntranceServices) -> HelpServices:
    """Return the Entrance's help, or refuse: no help pages on this Hive.

    Args:
        services: The Entrance's services.

    Returns:
        The help services.

    Raises:
        HelpNotServedError: The Entrance was built without help pages.
    """
    if services.help is None:
        raise HelpNotServedError()
    return services.help


async def ask_help(
    caller: CallerParam, services: Services, query: Annotated[HelpAskQuery, Query()]
) -> HelpAnswerView:
    """Answer the caller's question from the help pages.

    Args:
        caller: The admitted caller; its session is what the question budget counts.
        services: The Entrance's services.
        query: The question.

    Returns:
        The answer, or the passages alone, with the outcome saying which.

    Raises:
        HelpNotServedError: No help pages on this Hive (404).
        QuestionOverBudgetError: The session is over its questions for the minute (429).
    """
    # The token's hash names the session: never the token, and apart from the device's rate.
    return await ask(help_of(services), caller.session.session.token_hash, query.q)


HELP_ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec(
        method="GET",
        path=HELP_PATH,
        listeners=BOTH_LISTENERS,
        access=session_with(None),
        effect=RouteEffect.READ,
        endpoint=ask_help,
        summary="Ask the help: an answer from the help pages, with the sections it cites.",
        response_model=HelpAnswerView,
        mounted_when=Switch.HELP,
        refusals=HELP_REFUSALS,
    ),
)
