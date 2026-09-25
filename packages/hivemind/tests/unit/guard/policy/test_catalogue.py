"""Tests for hivemind.guard.policy.catalogue: every trail kind placed, every built point wired.

Roadmap step 10.3 (ADR-0039, "Every state-changing action has a named enforcement point"). The
first test is the gap-closer: a new trail kind that nobody placed -- neither authorised at a point
nor explained as no action -- fails here, as does a kind placed twice or a placed kind the trail no
longer defines. The second half holds, in this file on purpose, the registry of call sites
(`"package.module:function"`) for every point that is not pending, and asserts that each site's
own source still names its `EnforcementPoint.<MEMBER>`: a refactor that drops a check fails loudly
here instead of silently leaving an action unguarded.

Fits into the Hive:
    Mirrors src/hivemind/guard/policy/catalogue.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.guard.policy.catalogue for the tables under test.
    - hivemind.pheromone.events.families for the vocabulary they classify.
"""

from __future__ import annotations

import importlib
import inspect
from collections import Counter

import pytest

from hivemind.guard.policy.catalogue import AUTHORISED_AT, NOT_ACTIONS, PENDING_POINTS, classify
from hivemind.guard.policy.points import EnforcementPoint
from hivemind.pheromone import EVENT_FAMILIES

# Every built enforcement point, and every function whose source must keep naming it. A point
# with several sites (a Worker's tool and the Capping refusal it mirrors) lists each one.
_CALL_SITES: dict[EnforcementPoint, tuple[str, ...]] = {
    EnforcementPoint.PLACEMENT: ("hivemind.queen.dispatcher.ready:_record_placement_failure",),
    EnforcementPoint.LEASE_CREATION: ("hivemind.wardens.ticks.lease:_may_lease",),
    EnforcementPoint.GRANT_ISSUE: ("hivemind.queen.dispatcher.grants:_binding_allowed",),
    EnforcementPoint.FORAGE_REQUEST: ("hivemind.queen.ticks.forage:_authorize_request",),
    EnforcementPoint.WARDEN_SPAWN: ("hivemind.queen.attach:attach_warden",),
    EnforcementPoint.TOOL_INVOCATION: (
        "hivemind.workers.tools.registry:ToolRegistry.execute",
        "hivemind.workers.tools.http:http_request",
        "hivemind.workers.tools.proposals:_record_refusal",
    ),
    EnforcementPoint.SESSION_OUTSIDE_SCRATCH: (
        "hivemind.workers.tools.session:read_file",
        "hivemind.workers.tools.proposals:_record_refusal",
    ),
    EnforcementPoint.SLOT_BINDING: ("hivemind.wardens.spawn.binding:authorize_binding",),
    EnforcementPoint.QUESTION_ROUTING: (
        "hivemind.workers.tools.ask:ask",
        "hivemind.wardens.ticks.questions:_routing_refusal",
        "hivemind.queen.questions:_may_reach_the_human",
    ),
    EnforcementPoint.COMB_SHIELD_EGRESS: ("hivemind.queen.dispatcher.acquire:_authorize_egress",),
    EnforcementPoint.TAINT_CLEAR: ("hivemind.memory.taint.clear:_clear_request",),
    EnforcementPoint.ENTRANCE_ROUTE: ("hivemind.entrance.gate.admit:authorise",),
    EnforcementPoint.QUARANTINE: ("hivemind.wardens.quarantine.authority:_request",),
    EnforcementPoint.ISOLATION: ("hivemind.queen.isolation.authority:_request",),
}


def _trail_kinds() -> set[str]:
    """Every kind the trail vocabulary defines, across every family."""
    return {kind for family in EVENT_FAMILIES.values() for kind in family.KINDS}


def _classified() -> list[str]:
    """Every kind either table names, duplicates kept, so a double placement can be counted."""
    return [kind for kind, _ in AUTHORISED_AT] + [kind for kind, _ in NOT_ACTIONS]


def test_every_trail_kind_is_classified() -> None:
    unclassified = _trail_kinds() - set(_classified())

    assert unclassified == set(), f"place each kind in the catalogue: {sorted(unclassified)}"


def test_no_kind_is_classified_twice() -> None:
    doubled = sorted(kind for kind, count in Counter(_classified()).items() if count > 1)

    assert doubled == []


def test_every_classified_kind_is_a_kind_the_trail_defines() -> None:
    stale = set(_classified()) - _trail_kinds()

    assert stale == set(), f"no trail family defines: {sorted(stale)}"


def test_classify_returns_the_point_or_the_reason() -> None:
    assert classify("forage.requested") is EnforcementPoint.FORAGE_REQUEST
    assert classify("warden.spawned") is EnforcementPoint.WARDEN_SPAWN
    assert classify("llm.rebound") is EnforcementPoint.SLOT_BINDING
    assert isinstance(classify("guard.denied"), str)
    with pytest.raises(KeyError):
        classify("nothing.defined")


def test_pending_points_and_the_call_sites_partition_every_point() -> None:
    pending, wired = set(PENDING_POINTS), set(_CALL_SITES)

    assert pending.isdisjoint(wired)
    assert pending | wired == set(EnforcementPoint)


def test_every_pending_point_names_the_step_that_wires_it() -> None:
    assert all("step" in when or "phase" in when for when in PENDING_POINTS.values())


@pytest.mark.parametrize(
    ("point", "site"),
    [(point, site) for point, sites in _CALL_SITES.items() for site in sites],
    ids=lambda value: value.value if isinstance(value, EnforcementPoint) else value,
)
def test_every_call_site_still_names_its_enforcement_point(
    point: EnforcementPoint, site: str
) -> None:
    module_name, _, qualified = site.partition(":")
    target: object = importlib.import_module(module_name)
    for attribute in qualified.split("."):
        target = getattr(target, attribute)

    source = inspect.getsource(target)  # type: ignore[arg-type]  # a function or a method

    assert f"EnforcementPoint.{point.name}" in source
