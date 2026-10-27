"""Unit tests for hivemind.wardens.nurse.strength: the Worker grade a Nurse is held against."""

from __future__ import annotations

import pytest
from builders.forage import make_source
from builders.llm import make_bound
from builders.observation import make_llm_call

from hivemind.forage import ForageMap
from hivemind.llm import BoundModel, FakeLLMProvider
from hivemind.pheromone import LlmEvent
from hivemind.wardens.nurse import worker_strength
from waggle.clock import FakeClock
from waggle.messages.forage.values import MAX_MODEL_GRADE

# A local model at grade 2 with a hosted grade-4 fallback, and a stronger grade-5 hosted model.
_MAP = (
    make_source(source_id="s-local", provider="local", model="small", grade=2),
    make_source(source_id="s-cloud", provider="cloud", model="big", grade=4),
    make_source(source_id="s-other", provider="other", model="huge", grade=5),
)


def _worker(fallback: bool = False) -> BoundModel:
    """Bind a Worker on local/small, optionally falling back to cloud/big."""
    tail = make_bound(provider=FakeLLMProvider(name="cloud"), model="big") if fallback else None
    return make_bound(provider=FakeLLMProvider(name="local"), model="small", fallback=tail)


def _spilled_event() -> LlmEvent:
    """A non-call llm event on the strongest provider: must never count."""
    return make_llm_call(provider="other").model_copy(update={"kind": "llm.spill"})


@pytest.mark.parametrize(
    ("fallback", "calls", "grade", "providers"),
    [
        # Only its own binding: the grade of that model.
        (False, (), 2, ("local",)),
        # Its chain's fallback counts, since a spill could have reached it.
        (True, (), 4, ("local", "cloud")),
        # A call that reached a stronger provider outside the chain raises the bar.
        (False, (make_llm_call(provider="other"),), 5, ("local", "other")),
        # Repeated calls on a known provider add nothing; a non-call event is ignored.
        (True, (make_llm_call(provider="local"), _spilled_event()), 4, ("local", "cloud")),
        # A call on a provider the map cannot grade means strength cannot be proved.
        (False, (make_llm_call(provider="mystery"),), MAX_MODEL_GRADE, ("local", "mystery")),
    ],
)
def test_worker_strength_counts_chains_and_calls(
    fallback: bool, calls: tuple[LlmEvent, ...], grade: int, providers: tuple[str, ...]
) -> None:
    strength = worker_strength([_worker(fallback)], calls, ForageMap(_MAP, clock=FakeClock()))

    assert (strength.grade, strength.providers) == (grade, providers)
