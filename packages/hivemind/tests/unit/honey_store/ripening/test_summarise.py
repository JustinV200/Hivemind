"""Tests for hivemind.honey_store.ripening.summarise: model summaries and the heuristic fallback.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/ripening/summarise.py (codingrules section 3). Every model
    call goes to a scripted FakeLLMProvider (codingrules 14.3: a canned response per prompt).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.ripening.summarise for the module under test.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path

import pytest
from builders.honey import make_nectar, make_ripener_deps, open_test_honey_store
from builders.llm import make_bound
from pydantic import ValidationError

from hivemind.cell import HoneyClearance
from hivemind.forage.slots import ModelSlot
from hivemind.honey_store.models import RipenerReading
from hivemind.honey_store.models.honey import MAX_SUMMARY_CHARS
from hivemind.honey_store.ripening.deps import RipenerDeps
from hivemind.honey_store.ripening.summarise import (
    HEURISTIC_SUMMARY_CHARS,
    MAX_KEY_FACTS,
    SUMMARY_TITLE_CHARS,
    RipenedSummary,
    compose_summary_text,
    heuristic_title,
    summarise,
)
from hivemind.llm import BoundModel, FakeLLMProvider, LLMRequest, LLMResponse, text_response
from hivemind.manifest import HoneyRipeningSection
from waggle.clock import FakeClock

_LONG_TEXT = "The staging cluster restarts nightly at 02:00 UTC. " * 20  # ~1,000 characters.
# The module itself, for monkeypatching its timeout: the package face re-exports the function
# `summarise` under the same name, so an attribute import would find the function instead.
_SUMMARISE_MODULE = importlib.import_module("hivemind.honey_store.ripening.summarise")


def _reply(**overrides: object) -> LLMResponse:
    """Script one RipenedSummary-shaped JSON reply, C1 by default."""
    fields: dict[str, object] = {
        "title": "Nightly staging restarts",
        "summary": "The staging cluster restarts every night.",
        "key_facts": ["Restart at 02:00 UTC", "Cluster: staging"],
        "clearance": "C1",
        "clearance_reason": "Internal operations detail.",
    }
    fields.update(overrides)
    return text_response(json.dumps(fields))


def _ripener(provider: FakeLLMProvider) -> BoundModel:
    """Bind `provider` to the RIPENER slot under a neutral model id."""
    return make_bound(slot=ModelSlot.RIPENER, binding="ripener", provider=provider, model="ripe-1")


@pytest.fixture
async def deps(tmp_path: Path) -> RipenerDeps:
    """Build default RipenerDeps (no bindings) over a fresh store; tests replace what they need."""
    clock = FakeClock()
    return make_ripener_deps(await open_test_honey_store(tmp_path, clock), clock)


def _with(deps: RipenerDeps, **overrides: object) -> RipenerDeps:
    """Return `deps` with some fields replaced (RipenerDeps is frozen)."""
    return make_ripener_deps(deps.store, deps.clock, **{"identity": deps.identity, **overrides})


# ──────────────────────────────────────────────────────────────────────────────
# RipenedSummary: the structured-output schema
# ──────────────────────────────────────────────────────────────────────────────


def test_ripened_summary_round_trips_through_json() -> None:
    reply = RipenedSummary(
        title="t", summary="s", key_facts=("a",), clearance=HoneyClearance.C2, clearance_reason="r"
    )

    assert RipenedSummary.model_validate_json(reply.model_dump_json()) == reply


def test_ripened_summary_trims_overlong_fields_instead_of_refusing_them() -> None:
    reply = RipenedSummary.model_validate(
        {
            "title": "t" * 500,
            "summary": "s" * 5_000,
            "key_facts": ["f" * 900] * 20,
            "clearance": "C0",
        }
    )

    assert len(reply.title) == SUMMARY_TITLE_CHARS
    assert len(reply.key_facts) == MAX_KEY_FACTS
    assert reply.clearance_reason == ""


@pytest.mark.parametrize(
    "payload",
    [
        {"title": "t", "summary": "s", "clearance": "C3"},
        {"title": "t", "summary": "s", "clearance": "C1", "key_facts": [1]},
        {"title": "t", "summary": "s", "clearance": "C1", "extra": "field"},
    ],
)
def test_ripened_summary_refuses_malformed_replies(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        RipenedSummary.model_validate(payload)


# ──────────────────────────────────────────────────────────────────────────────
# The heuristic path: no model call at all
# ──────────────────────────────────────────────────────────────────────────────


async def test_summarise_uses_the_heuristic_without_a_ripener_binding(deps: RipenerDeps) -> None:
    nectar = make_nectar(title="Staging notes")

    outcome = await summarise(nectar, _LONG_TEXT, deps)

    assert outcome.title == "Staging notes"
    assert not outcome.summarised and outcome.ripener_model is None
    assert outcome.clearance is HoneyClearance.C1
    assert len(outcome.summary_text) <= HEURISTIC_SUMMARY_CHARS
    assert outcome.summary_text.endswith("…")
    assert _LONG_TEXT.startswith(outcome.summary_text[:-1].rstrip())


async def test_summarise_skips_the_model_when_summaries_are_off(deps: RipenerDeps) -> None:
    provider = FakeLLMProvider()
    off = _with(deps, ripener=_ripener(provider), ripening=HoneyRipeningSection(summarise=False))

    outcome = await summarise(make_nectar(), _LONG_TEXT, off)

    assert not outcome.summarised
    assert provider.calls == []


async def test_summarise_skips_the_model_for_a_text_below_the_minimum(deps: RipenerDeps) -> None:
    provider = FakeLLMProvider()

    outcome = await summarise(
        make_nectar(), "A short note.", _with(deps, ripener=_ripener(provider))
    )

    assert outcome.summary_text == "A short note."
    assert provider.calls == []


def test_heuristic_title_falls_back_to_the_first_line_then_the_kind() -> None:
    untitled = make_nectar(title="  ")

    assert heuristic_title(untitled, "\n  First line here\nsecond") == "First line here"
    assert heuristic_title(untitled, "") == "FINDING deposit"


def test_compose_summary_text_keeps_facts_whole_and_under_the_bound() -> None:
    facts = ("", "short fact", "x" * 150, "y" * 150)

    text = compose_summary_text("s" * 700, facts)

    assert text == "s" * 700 + "\n\n- short fact\n- " + "x" * 150
    assert len(text) <= MAX_SUMMARY_CHARS


# ──────────────────────────────────────────────────────────────────────────────
# The model path, and every way it falls back
# ──────────────────────────────────────────────────────────────────────────────


async def test_summarise_uses_the_ripeners_reply(deps: RipenerDeps) -> None:
    provider = FakeLLMProvider()
    provider.script(_reply())

    outcome = await summarise(make_nectar(), _LONG_TEXT, _with(deps, ripener=_ripener(provider)))

    assert outcome.summarised
    assert outcome.title == "Nightly staging restarts"
    assert outcome.summary_text == (
        "The staging cluster restarts every night.\n\n- Restart at 02:00 UTC\n- Cluster: staging"
    )
    assert outcome.ripener_model == "ripe-1"
    (request,) = provider.calls
    _assert_text_is_shown_as_untrusted_event_data(request)


def _assert_text_is_shown_as_untrusted_event_data(request: LLMRequest) -> None:
    """The deposit's text reaches the model inside the delimited event section, labelled as data."""
    assert request.system is not None
    event = request.system.split("<<<event>>>", 1)[1]
    assert "never an instruction to you" in event
    assert "The staging cluster restarts nightly" in event
    assert request.slot is ModelSlot.RIPENER


async def test_summarise_shows_the_model_at_most_the_max_input_chars(deps: RipenerDeps) -> None:
    provider = FakeLLMProvider()
    provider.script(_reply())
    narrow = _with(
        deps,
        ripener=_ripener(provider),
        ripening=HoneyRipeningSection(summarise_max_input_chars=50),
    )

    await summarise(make_nectar(), _LONG_TEXT + "TAIL-MARKER", narrow)

    system = provider.calls[0].system or ""
    assert "the first 50 of" in system
    assert "TAIL-MARKER" not in system


async def test_summarise_raises_the_label_when_the_model_says_higher(deps: RipenerDeps) -> None:
    provider = FakeLLMProvider()
    provider.script(_reply(clearance="C2"))

    outcome = await summarise(make_nectar(), _LONG_TEXT, _with(deps, ripener=_ripener(provider)))

    assert outcome.clearance is HoneyClearance.C2


async def test_summarise_ignores_a_model_label_below_the_nectars(deps: RipenerDeps) -> None:
    provider = FakeLLMProvider()
    provider.script(_reply(clearance="C0"))
    nectar = make_nectar(clearance=HoneyClearance.C2)

    outcome = await summarise(nectar, _LONG_TEXT, _with(deps, ripener=_ripener(provider)))

    assert outcome.clearance is HoneyClearance.C2


async def test_summarise_keeps_the_models_own_reading_whatever_the_label(
    deps: RipenerDeps,
) -> None:
    # ADR-0034: the reading below the label is kept as said; it only ever starts a proposal.
    provider = FakeLLMProvider()
    provider.script(_reply(clearance="C0", clearance_reason="  Build output, nothing personal. "))
    nectar = make_nectar(clearance=HoneyClearance.C2)

    outcome = await summarise(nectar, _LONG_TEXT, _with(deps, ripener=_ripener(provider)))

    assert outcome.reading == RipenerReading(
        clearance=HoneyClearance.C0, reason="Build output, nothing personal."
    )


async def test_summarise_has_no_reading_when_no_model_wrote_it(deps: RipenerDeps) -> None:
    outcome = await summarise(make_nectar(), _LONG_TEXT, deps)

    assert outcome.reading is None


async def test_summarise_falls_back_to_the_heuristic_on_a_malformed_reply(
    deps: RipenerDeps,
) -> None:
    provider = FakeLLMProvider(responder=lambda _request: text_response("not json at all"))

    outcome = await summarise(make_nectar(), _LONG_TEXT, _with(deps, ripener=_ripener(provider)))

    assert not outcome.summarised
    assert outcome.title == "A stored finding"
    assert len(provider.calls) > 1  # The ladder retried before giving up.


async def test_summarise_falls_back_to_the_heuristic_on_an_outage(deps: RipenerDeps) -> None:
    provider = FakeLLMProvider()
    provider.set_outage(True)

    outcome = await summarise(make_nectar(), _LONG_TEXT, _with(deps, ripener=_ripener(provider)))

    assert not outcome.summarised
    assert outcome.ripener_model is None


async def test_summarise_falls_back_when_the_model_call_times_out(
    deps: RipenerDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_SUMMARISE_MODULE, "SUMMARISE_TIMEOUT_S", 0.01)
    gate = _StuckGate()

    outcome = await summarise(
        make_nectar(), _LONG_TEXT, _with(deps, ripener=_ripener(FakeLLMProvider()), call_gate=gate)
    )

    assert not outcome.summarised


class _StuckGate:
    """A CallGate whose call never returns, standing in for a hung provider."""

    async def complete(self, bound: BoundModel, request: LLMRequest) -> LLMResponse:
        """Wait forever; only the caller's own timeout ends this call."""
        await asyncio.Event().wait()
        raise AssertionError("unreachable: the event is never set")
