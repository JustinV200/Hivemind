"""Unit tests for hivemind.wardens.nurse.eligible: which JUDGE links may review an attempt."""

from __future__ import annotations

import pytest
from builders.capping import make_nurse_request
from builders.forage import make_source
from builders.llm import make_bound

from hivemind.forage import ForageMap
from hivemind.forage.slots import ModelSlot
from hivemind.llm import BoundModel, FakeLLMProvider
from hivemind.wardens.nurse import CallSize, nurse_chain
from waggle.clock import FakeClock

_SIZE = CallSize(input_tokens=1_000, output_tokens=1_000)
_MAP = ForageMap(
    (
        make_source(source_id="s-a", provider="anthropic", model="top", grade=5),
        make_source(source_id="s-b", provider="other", model="mid", grade=3),
        make_source(source_id="s-c", provider="cheap", model="low", grade=2),
    ),
    clock=FakeClock(),
)


def _judge(*links: tuple[str, str], **overrides: object) -> BoundModel:
    """Chain JUDGE links in the order given, each (provider, model), with `overrides` on all."""
    chain: BoundModel | None = None
    for provider, model in reversed(links):
        chain = make_bound(
            slot=ModelSlot.JUDGE,
            provider=FakeLLMProvider(name=provider),
            model=model,
            fallback=chain,
            **overrides,
        )
    assert chain is not None
    return chain


def _picked(judge: BoundModel, **request: object) -> list[str] | None:
    """Return the eligible chain's providers in order, or None when none is eligible."""
    found = nurse_chain(judge, make_nurse_request(**request), _MAP, _SIZE)
    return None if found is None else [link.provider.name for link in found.bound.chain()]


@pytest.mark.parametrize(
    ("judge", "request_fields", "expected"),
    [
        # Weaker than the grade-3 Worker, or a model the map cannot grade: never eligible.
        (_judge(("cheap", "low"), ("ghost", "unmapped")), {}, None),
        # The identical top model in a fresh context counts as equal.
        (_judge(("anthropic", "top")), {"worker_grade": 5}, ["anthropic"]),
        # A provider the Worker never used goes first; chain order otherwise; weak links drop.
        (
            _judge(("anthropic", "top"), ("cheap", "low"), ("other", "mid")),
            {},
            ["other", "anthropic"],
        ),
        # A call that could cost more than the task has left is skipped (this one: about $0.009).
        (_judge(("other", "mid")), {"spend_left": 0.001}, None),
        (_judge(("other", "mid")), {"spend_left": 1.0}, ["other"]),
        # An unpriced model cannot be estimated, so the budget never rules it out.
        (
            _judge(
                ("other", "mid"), cost_per_million_input_usd=None, cost_per_million_output_usd=None
            ),
            {"spend_left": 0.0},
            ["other"],
        ),
        # A model whose window cannot hold the prompt and the reply cannot review it.
        (_judge(("other", "mid"), context_window=1_500), {}, None),
    ],
)
def test_nurse_chain_keeps_only_eligible_links_in_preference_order(
    judge: BoundModel, request_fields: dict[str, object], expected: list[str] | None
) -> None:
    assert _picked(judge, **request_fields) == expected


def test_nurse_chain_records_the_weakest_eligible_grade() -> None:
    # The ladder does not say which link answered, so the review claims the weakest one.
    found = nurse_chain(
        _judge(("anthropic", "top"), ("other", "mid")), make_nurse_request(), _MAP, _SIZE
    )

    assert found is not None
    assert found.grade == 3
