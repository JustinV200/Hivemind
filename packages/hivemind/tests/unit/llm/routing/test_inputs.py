"""Tests for hivemind.llm.routing.inputs: RoutingInputs.

Fits into the Hive:
    Mirrors src/hivemind/llm/routing/inputs.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.routing.inputs for the module under test.
"""

from __future__ import annotations

import dataclasses

import pytest
from builders.routing import make_chain, make_chain_source

from hivemind.forage import ForageMap, ModelSlot, Tempo
from hivemind.llm.routing import RoutingInputs, RoutingRules
from waggle.clock import FakeClock


def test_from_map_snapshots_the_maps_sources_with_empty_optional_inputs() -> None:
    source = make_chain_source("a")
    forage = ForageMap([source], clock=FakeClock())

    inputs = RoutingInputs.from_map(
        forage, ModelSlot.WORKER, Tempo(), make_chain("a"), RoutingRules()
    )

    assert inputs.sources == (source,)
    assert (inputs.cell_id, inputs.hosting) == (None, None)
    assert inputs.required_capabilities == frozenset()
    assert inputs.local_providers == frozenset()


async def test_from_map_is_a_snapshot_a_later_map_change_does_not_alter() -> None:
    source = make_chain_source("a")
    forage = ForageMap([source], clock=FakeClock())
    inputs = RoutingInputs.from_map(
        forage, ModelSlot.WORKER, Tempo(), make_chain("a"), RoutingRules()
    )

    await forage.remove_source(source.source_id)

    assert inputs.sources == (source,)


def test_routing_inputs_are_frozen() -> None:
    inputs = RoutingInputs.from_map(
        ForageMap([], clock=FakeClock()), ModelSlot.WORKER, Tempo(), make_chain("a"), RoutingRules()
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        inputs.slot = ModelSlot.QUEEN  # type: ignore[misc]
