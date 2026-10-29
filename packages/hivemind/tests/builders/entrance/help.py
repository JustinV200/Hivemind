"""Build the help's Ask for a serving rig, and ask it a question as a device does.

The help's Ask (ADR-0069) is exercised over the builders' serving rig with a small corpus of two
pages (``PAGES``), a keyword retriever over it, and, when a test hands one in, a scripted
``FakeLLMProvider`` as the answerer's model on an unmetered gate (the rig meters nothing, so the
fake's ``calls`` are each question a model saw). ``help_services`` builds the services on their
own for the door's and the compose tests; ``help_rig`` wraps them in the rig's options; ``ask``
sends one question on ``GET /v1/help/ask``; ``ASKER`` is a device holding ``observe`` alone,
the least any session holds.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the tests under
    packages/hivemind/tests/unit/entrance/help.

Key invariants:
    - ``PAGES`` has the shape the front end's pages have: an opening paragraph, ``##`` sections,
      a ``###`` inside one, bold, code and a link, so the corpus test reads real markup.
"""

from __future__ import annotations

from urllib.parse import urlencode

import httpx
from builders.entrance.landing import LandingClient, LandingSession
from builders.entrance.serving import ProgramGrant, RigOptions
from builders.llm import make_bound

from hivemind.entrance.auth.limits import QuestionBudget
from hivemind.entrance.help import QUESTIONS_PER_MINUTE, HelpAnswerer, HelpServices
from hivemind.entrance.help.retrieval import KeywordRetriever, corpus_from_pages
from hivemind.forage import ModelSlot
from hivemind.llm import DirectCallGate, FakeLLMProvider
from waggle.clock import Clock, SystemClock

ASK = "/v1/help/ask"  # The help's route.
ASKER = ProgramGrant(capabilities=("observe",))  # The least a session holds: enough to ask.
# Two small pages in the front end's own markup: what every help test retrieves over.
PAGES: dict[str, str] = {
    "glossary": (
        "Every bee term, in plain language.\n"
        "\n"
        "## Night Veil\n"
        "\n"
        "The **sealed** tier: a Virtual Cell behind a VPN and Tor, destroyed when its task ends.\n"
        "Nothing it did survives but the lifecycle skeleton.\n"
        "\n"
        "## Warden\n"
        "\n"
        "One supervisor per Cell. It spawns Workers within its grant and never provisions a Cell.\n"
        "\n"
        "### What a Warden watches\n"
        "\n"
        "Telemetry, Alarms and the lease; see [Fleet](#/fleet) for each one's `mode`.\n"
    ),
    "screens": (
        "How to read each screen.\n"
        "\n"
        "## Fleet\n"
        "\n"
        "Every Cell in one table. Click a column header to sort; on a phone use Sort by.\n"
        "\n"
        "## Cost\n"
        "\n"
        "Spend per goal, per slot and per provider, two decimals.\n"
    ),
}

__all__ = ["ASK", "ASKER", "PAGES", "ask", "help_rig", "help_services"]


def help_services(
    provider: FakeLLMProvider | None,
    clock: Clock | None = None,
    *,
    questions_per_minute: int = QUESTIONS_PER_MINUTE,
) -> HelpServices:
    """Build the help's services over ``PAGES``: keyword retrieval, ``provider`` as the model.

    Args:
        provider: The answerer's scripted model; None builds no answerer (links only).
        clock: The budget's clock; the system clock when omitted.
        questions_per_minute: The session budget.

    Returns:
        The services.
    """
    answerer = (
        None
        if provider is None
        else HelpAnswerer(
            make_bound(provider=provider, slot=ModelSlot.ATTENDANT, binding="attendant"),
            DirectCallGate(),
        )
    )
    return HelpServices(
        retriever=KeywordRetriever(corpus_from_pages(PAGES)),
        answerer=answerer,
        budget=QuestionBudget(clock or SystemClock(), questions_per_minute),
    )


def help_rig(
    provider: FakeLLMProvider | None = None, questions_per_minute: int = QUESTIONS_PER_MINUTE
) -> RigOptions:
    """Return a rig's options serving the Ask over ``PAGES``.

    Args:
        provider: The answerer's scripted model; None serves links only.
        questions_per_minute: The session budget.

    Returns:
        The options.
    """
    return RigOptions(help=help_services(provider, questions_per_minute=questions_per_minute))


async def ask(client: LandingClient, session: LandingSession, question: str) -> httpx.Response:
    """Send ``question`` to ``GET /v1/help/ask`` as the session.

    Args:
        client: The device's client.
        session: Its session.
        question: The question, placed in the query.

    Returns:
        The response, whatever its status.
    """
    return await client.call(session, "GET", f"{ASK}?{urlencode({'q': question})}")
