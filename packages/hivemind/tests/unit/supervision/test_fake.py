"""Tests for hivemind.supervision.fake: FakeSupervisor.

Fits into the Hive:
    Mirrors src/hivemind/supervision/fake.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.supervision.fake for the module under test.
"""

from __future__ import annotations

import pytest
from builders.supervision import make_telemetry

from hivemind.supervision.errors import UnknownChildError
from hivemind.supervision.fake import FakeSupervisor
from hivemind.supervision.intervention import Cancel
from hivemind.supervision.supervisor import ChildKind, ChildRef
from waggle.messages.supervision import CompactView

_CHILD = ChildRef(id="worker_1", kind=ChildKind.WORKER, task_id=None, state="RUNNING")
_VIEW = CompactView(goal="Do the thing.", progress="Started.", decisions=(), open_threads=())


async def test_children_returns_what_it_was_built_with() -> None:
    supervisor = FakeSupervisor(children=(_CHILD,))

    assert await supervisor.children() == (_CHILD,)


async def test_children_defaults_to_empty() -> None:
    supervisor = FakeSupervisor()

    assert await supervisor.children() == ()


async def test_telemetry_returns_the_scripted_value() -> None:
    telemetry = make_telemetry()
    supervisor = FakeSupervisor(children=(_CHILD,), telemetry={"worker_1": telemetry})

    assert await supervisor.telemetry("worker_1") == telemetry


async def test_telemetry_raises_for_an_unknown_child() -> None:
    supervisor = FakeSupervisor(children=(_CHILD,))

    with pytest.raises(UnknownChildError):
        await supervisor.telemetry("worker_unknown")


async def test_inspect_returns_the_scripted_view() -> None:
    supervisor = FakeSupervisor(children=(_CHILD,), views={"worker_1": _VIEW})

    assert await supervisor.inspect("worker_1") == _VIEW


async def test_inspect_raises_for_an_unknown_child() -> None:
    supervisor = FakeSupervisor(children=(_CHILD,))

    with pytest.raises(UnknownChildError):
        await supervisor.inspect("worker_unknown")


async def test_intervene_records_the_call_on_interventions() -> None:
    supervisor = FakeSupervisor(children=(_CHILD,))
    intervention = Cancel(reason="the goal was withdrawn")

    await supervisor.intervene("worker_1", intervention)

    assert supervisor.interventions == [("worker_1", intervention)]


async def test_intervene_raises_for_an_unknown_child_and_records_nothing() -> None:
    supervisor = FakeSupervisor(children=(_CHILD,))

    with pytest.raises(UnknownChildError):
        await supervisor.intervene("worker_unknown", Cancel(reason="r"))

    assert supervisor.interventions == []


async def test_interventions_accumulate_in_call_order() -> None:
    supervisor = FakeSupervisor(children=(_CHILD,))
    first = Cancel(reason="first")
    second = Cancel(reason="second")

    await supervisor.intervene("worker_1", first)
    await supervisor.intervene("worker_1", second)

    assert supervisor.interventions == [("worker_1", first), ("worker_1", second)]
