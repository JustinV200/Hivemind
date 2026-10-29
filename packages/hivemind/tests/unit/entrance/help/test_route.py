"""Test hivemind.entrance.help.route: GET /v1/help/ask over real listeners.

Any session may ask, and is answered with the model's words and the sections to open; a session
past its budget is 429; a question the query model does not take is 422; a Hive served without
help pages has no route at all (404). The committed contract describes the row.

Fits into the Hive:
    Mirrors src/hivemind/entrance/help/route.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import json

from builders.entrance.help import ASK, ASKER, ask, help_rig
from builders.entrance.serving import RigOptions, serving

from hivemind.entrance.gate import RouteEffect, Switch
from hivemind.entrance.help import HELP_ROUTES, HelpOutcome
from hivemind.entrance.landing_board import render_document
from hivemind.llm import FakeLLMProvider, text_response


async def test_any_session_is_answered_with_the_words_and_the_sections_to_open() -> None:
    provider = FakeLLMProvider()
    provider.script(text_response("Click a column header to sort [1]."))
    async with serving(help_rig(provider)) as rig:
        client, session = await rig.program(ASKER)

        answered = await ask(client, session, "How do I sort the Fleet table?")

    assert answered.status_code == 200, answered.text
    body = answered.json()
    assert (body["outcome"], body["answer"]) == (
        HelpOutcome.ANSWERED.value,
        "Click a column header to sort [1].",
    )
    assert body["sources"][0] == {
        "page": "screens",
        "section": "fleet",
        "heading": "Fleet",
        "snippet": "Every Cell in one table. Click a column header to sort; on a phone use "
        "Sort by.",
        "cited": True,
    }
    assert body["slot"] == "ATTENDANT"


async def test_a_session_past_its_budget_is_refused_without_a_model_call() -> None:
    provider = FakeLLMProvider()
    provider.script(text_response("One [1]."), text_response("Two [1]."))
    async with serving(help_rig(provider, questions_per_minute=1)) as rig:
        client, session = await rig.program(ASKER)
        other, other_session = await rig.program(ASKER)

        first = await ask(client, session, "What is the Night Veil?")
        second = await ask(client, session, "What is the Night Veil?")
        elsewhere = await ask(other, other_session, "What is the Night Veil?")

    assert [r.status_code for r in (first, second, elsewhere)] == [200, 429, 200]
    assert second.json()["error"] == "hivemind.entrance.help_over_budget"
    assert len(provider.calls) == 2


async def test_a_question_the_route_does_not_take_is_refused() -> None:
    # One case only: the query's bounds are tabled in test_models; this proves the gate's body.
    async with serving(help_rig(FakeLLMProvider())) as rig:
        client, session = await rig.program(ASKER)

        refused = await client.call(session, "GET", ASK)

    assert refused.status_code == 422, refused.text
    assert refused.json()["error"] == "hivemind.entrance.invalid_request"


async def test_a_hive_served_without_help_pages_has_no_route() -> None:
    async with serving(RigOptions()) as rig:
        client, session = await rig.program(ASKER)

        missing = await ask(client, session, "What is the Night Veil?")

    assert missing.status_code == 404


def test_the_row_is_a_read_any_session_may_make_and_the_document_describes_it() -> None:
    [row] = HELP_ROUTES

    assert (row.method, row.path, row.effect) == ("GET", ASK, RouteEffect.READ)
    assert (row.access.needs(), row.mounted_when) == ((), Switch.HELP)
    operation = json.loads(render_document())["paths"][ASK]["get"]
    assert {"q"} == {p["name"] for p in operation["parameters"]}
    assert "429" in operation["responses"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/HelpAnswerView"
    }
