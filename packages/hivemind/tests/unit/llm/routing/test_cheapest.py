"""Test hivemind.llm.routing.cheapest: the chat row the Forage map prices lowest.

A free local fallback beats every priced hosted row; with no free row the cheapest priced one
wins; a row the map does not price ranks last; the non-chat rows (embedder, transcriber) are never
chosen however cheap, and a table with no chat row gives None.

Fits into the Hive:
    Mirrors src/hivemind/llm/routing/cheapest.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import pytest
from builders.forage import make_source
from builders.llm import make_binding

from hivemind.forage import ModelCost, ModelSource, SlotBinding
from hivemind.llm.routing import cheapest_chat_binding


def _priced(provider: str, model: str, grade: int, in_usd: float, out_usd: float) -> ModelSource:
    """A map source for ``provider``/``model`` at ``grade`` with the given token prices."""
    cost = ModelCost(cost_per_million_input_usd=in_usd, cost_per_million_output_usd=out_usd)
    return make_source(source_id=model, provider=provider, model=model, grade=grade, cost=cost)


# The shape docs/manifests/full.toml has: a strong Queen, a cheap Attendant, a Worker with a
# free local fallback, and the two non-chat slots bound to a free local server.
ROWS: tuple[SlotBinding, ...] = (
    make_binding(key="queen", provider="hosted", model="big"),
    make_binding(key="attendant", provider="hosted", model="small"),
    make_binding(key="worker", provider="hosted", model="mid", fallback="local_worker"),
    make_binding(key="local_worker", provider="local", model="tiny"),
    make_binding(key="embedder", provider="local", model="vectors"),
    make_binding(key="transcriber", provider="local", model="ears"),
)
HOSTED = (
    _priced("hosted", "big", 5, 5.0, 25.0),
    _priced("hosted", "mid", 4, 2.0, 10.0),
    _priced("hosted", "small", 3, 1.0, 5.0),
)
LOCAL = (
    _priced("local", "tiny", 2, 0.0, 0.0),
    _priced("local", "vectors", 1, 0.0, 0.0),
    _priced("local", "ears", 1, 0.0, 0.0),
)


@pytest.mark.parametrize(
    ("sources", "expected"),
    [
        (HOSTED + LOCAL, "local_worker"),  # Free beats priced; the non-chat rows never count.
        (HOSTED, "attendant"),  # Nothing free priced: the cheapest hosted row.
        ((), "queen"),  # Nothing priced at all: manifest order, the first chat slot's row.
    ],
    ids=["free-local", "cheapest-hosted", "unpriced"],
)
def test_the_lowest_priced_reachable_chat_row_wins(
    sources: tuple[ModelSource, ...], expected: str
) -> None:
    chosen = cheapest_chat_binding(ROWS, sources)

    assert chosen is not None and chosen.key == expected


def test_no_chat_row_gives_none() -> None:
    non_chat = tuple(row for row in ROWS if row.key in {"embedder", "transcriber"})

    assert cheapest_chat_binding(non_chat, LOCAL) is None
