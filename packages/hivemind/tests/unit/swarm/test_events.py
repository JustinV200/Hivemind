"""Tests for hivemind.swarm.events: the one place the Swarm builds and records its trail events.

Fits into the Hive:
    Mirrors src/hivemind/swarm/events.py (codingrules section 3: tests/unit mirrors src/
    one-to-one). Every Swarm component records through this recorder, so the properties that keep
    the trail safe (valid kinds, a waggle id as the subject, no text-bearing payload keys, a failed
    write that is never swallowed) are shown here once.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.events for the module under test.
    - .claude/codingrules.md sections 12 and 15 for what the trail holds and never holds.
"""

from __future__ import annotations

import pytest
from builders.cells import make_identity
from pydantic import ValidationError

from hivemind.pheromone import (
    GuardEvent,
    MemoryPheromoneTrail,
    PheromoneEvent,
    SwarmEvent,
    TrailQuery,
)
from hivemind.swarm.events import SwarmRecorder
from waggle.clock import FakeClock
from waggle.ids import new_device_id, new_hive_id


class _DownTrail(MemoryPheromoneTrail):
    """A trail whose store is unavailable."""

    async def record(self, event: PheromoneEvent) -> None:
        """Fail like a database that cannot be written."""
        raise OSError("the trail is down")


def _recorder(clock: FakeClock, trail: MemoryPheromoneTrail | None = None) -> SwarmRecorder:
    """A recorder over `trail` (a fresh one when omitted) and a fresh identity."""
    return SwarmRecorder(trail or MemoryPheromoneTrail(clock), clock, make_identity(clock))


def test_a_swarm_event_is_stamped_now_as_the_recorders_own_identity() -> None:
    clock = FakeClock()
    recorder = _recorder(clock)
    subject = new_device_id(clock)

    event = recorder.swarm_event("swarm.online", subject, {"reason": "link_connected"})

    assert isinstance(event, SwarmEvent) and event.kind == "swarm.online"
    assert event.at == clock.now() and event.subject_id == subject
    assert (event.hive_id, event.node_id) == (recorder.identity.hive_id, recorder.identity.node_id)
    assert event.actor == "system" and event.payload == {"reason": "link_connected"}


def test_an_operators_act_is_recorded_as_the_human_who_did_it() -> None:
    clock = FakeClock()

    event = _recorder(clock).swarm_event("swarm.revoked", new_device_id(clock), {}, actor="human")

    assert event.actor == "human"


def test_a_guard_event_is_always_the_systems_own() -> None:
    clock = FakeClock()

    event = _recorder(clock).guard_event(
        "guard.frame_ceiling", new_hive_id(clock), {"node_id": None}
    )

    assert isinstance(event, GuardEvent) and event.actor == "system"


def test_every_event_has_a_fresh_id() -> None:
    clock = FakeClock()
    recorder, subject = _recorder(clock), new_device_id(clock)

    ids = {recorder.swarm_event("swarm.online", subject, {}).id for _ in range(20)}

    assert len(ids) == 20


@pytest.mark.parametrize("kind", ["guard.frame_ceiling", "swarm.not_a_kind", "online", ""])
def test_a_kind_outside_the_swarm_vocabulary_is_rejected(kind: str) -> None:
    clock = FakeClock()

    with pytest.raises(ValidationError):
        _recorder(clock).swarm_event(kind, new_device_id(clock), {})


def test_a_guard_event_must_be_in_the_guard_vocabulary() -> None:
    clock = FakeClock()

    with pytest.raises(ValidationError):
        _recorder(clock).guard_event("swarm.online", new_hive_id(clock), {})


def test_a_subject_that_is_not_a_waggle_id_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _recorder(FakeClock()).swarm_event("swarm.online", "kitchen-pi", {})


@pytest.mark.parametrize(
    "key",
    ["prompt", "completion", "messages", "history", "transcript", "content", "text", "output"],
)
def test_a_payload_that_carries_text_the_trail_never_holds_is_rejected(key: str) -> None:
    clock = FakeClock()

    with pytest.raises(ValidationError):
        _recorder(clock).swarm_event("swarm.online", new_device_id(clock), {key: "x"})


async def test_building_an_event_writes_nothing_and_recording_it_writes_it() -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    recorder = _recorder(clock, trail)
    event = recorder.swarm_event("swarm.online", new_device_id(clock), {})

    assert await trail.query(TrailQuery()) == ()
    await recorder.record(event)
    assert await trail.query(TrailQuery()) == (event,)


async def test_a_failed_write_reaches_the_caller_so_nothing_is_believed_recorded() -> None:
    clock = FakeClock()
    recorder = _recorder(clock, _DownTrail(clock))
    event = recorder.swarm_event("swarm.online", new_device_id(clock), {})

    with pytest.raises(OSError, match="the trail is down"):
        await recorder.record(event)
