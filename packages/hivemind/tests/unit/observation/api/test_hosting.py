"""Test hivemind.observation.api.hosting: where a Cell's models come from, from its plan.

A Cell with no plan is unknown; a plan whose primaries all run on the Cell is local, on the Hive
Stand's Cell hive_stand, on a provider hosted; a mix is hybrid; a plan naming only sources the map
lacks is unknown again.

Fits into the Hive:
    Mirrors src/hivemind/observation/api/hosting.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from builders.cells import make_cell
from builders.forage import make_source

from hivemind.forage import HostingPlan, ModelSlot, SlotPlan, SourceChain
from hivemind.observation import HostingFacts, hosting_facts, hosting_view, models_from
from waggle.clock import FakeClock

_CLOCK = FakeClock()
_CELL, _STAND = make_cell(clock=_CLOCK).id, make_cell(clock=_CLOCK).id


def _plan(cell_id: str, default: str, **slots: str) -> HostingPlan:
    """A plan for ``cell_id`` whose default and named slots each have one primary source."""
    return HostingPlan(
        cell_id=cell_id,
        revision=3,
        reason="test",
        slots=tuple(
            SlotPlan(slot=ModelSlot(name.upper()), chain=SourceChain(primary=source))
            for name, source in slots.items()
        ),
        default=SourceChain(primary=default),
    )


def _facts(plan: HostingPlan | None) -> HostingFacts:
    """Facts over three sources: this Cell's server, the Hive Stand's and a hosted provider."""
    sources = [
        make_source(source_id="mine", host_cell_id=_CELL),
        make_source(source_id="stand", host_cell_id=_STAND),
        make_source(source_id="cloud", host_cell_id=None),
    ]
    return hosting_facts([plan] if plan is not None else [], sources, [_STAND])


def test_a_cell_with_no_plan_is_unknown_and_lists_no_slot() -> None:
    view = hosting_view(_CELL, _facts(None))

    assert (view.models_from, view.plan_revision, view.slots) == ("unknown", None, [])


def test_each_place_a_primary_runs_is_named_for_the_cell() -> None:
    assert models_from(_CELL, _facts(_plan(_CELL, "mine"))) == "local"
    assert models_from(_CELL, _facts(_plan(_CELL, "stand"))) == "hive_stand"
    assert models_from(_CELL, _facts(_plan(_CELL, "cloud"))) == "hosted"


def test_mixed_places_are_hybrid_and_each_slot_is_listed() -> None:
    view = hosting_view(_CELL, _facts(_plan(_CELL, "cloud", worker="mine")))

    assert view.models_from == "hybrid"
    assert view.plan_revision == 3
    assert [(s.slot, s.primary_source, s.models_from) for s in view.slots] == [
        (ModelSlot.WORKER, "mine", "local")
    ]


def test_a_plan_naming_only_sources_the_map_lacks_is_unknown() -> None:
    view = hosting_view(_CELL, _facts(_plan(_CELL, "gone", worker="gone")))

    assert view.models_from == "unknown"
    assert [slot.models_from for slot in view.slots] == ["unknown"]
