"""The trail event view carries an llm call's usage, its locality included.

Fits into the Hive:
    Unit tests for `hivemind.observation.views.trail`.
"""

from __future__ import annotations

from builders.trail import make_llm_call, make_trail_event

from hivemind.observation.views import trail_event_view
from hivemind.pheromone import LlmUsage


def test_a_local_calls_usage_says_so_in_the_view() -> None:
    usage = LlmUsage(input_tokens=10, output_tokens=5, cached_tokens=0, cost_usd=0.2, local=True)
    event = make_trail_event("llm.call", slot="WORKER", provider="local", usage=usage)

    view = trail_event_view(event)

    assert view.usage is not None
    assert view.usage.local is True
    assert view.usage.cost_usd == 0.2


def test_a_call_that_never_said_local_reads_as_hosted() -> None:
    view = trail_event_view(make_llm_call(cost_usd=0.5))

    assert view.usage is not None
    assert view.usage.local is False


def test_a_non_llm_event_carries_no_usage() -> None:
    view = trail_event_view(make_trail_event("task.assigned"))

    assert view.usage is None and view.slot is None and view.provider is None
