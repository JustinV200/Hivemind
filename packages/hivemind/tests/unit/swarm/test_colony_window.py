"""Unit: how the unnoticed-window scenario listens at the Queen's replay window.

The scenario (`tests/e2e/test_phase11_exit_unnoticed.py`) must show that a frame the Warden sent
again was dropped by the window, and the trail cannot say so: a duplicate inside the reconnection
grace is dropped with no event. So it listens where the drop is decided, at the callback the
swarm listener's transport hands each refusal to. What that listening must not do is checked here
in milliseconds: it hears a silent drop, it changes what the callback records, and it is gone when
the block ends.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped. Exercises
    `e2e.swarm.colony.window`; nothing here starts a container or touches a network.

Key invariants:
    - The real `TransportRefusals` runs on every refusal, over stand-ins for the two collaborators
      it reads (the hub's attach time and the trail's recorder), so each rule is tested alone.
    - No test sleeps.

See Also:
    - tests/unit/swarm/gateway/test_refusals.py for the callback itself.
"""

from __future__ import annotations

from datetime import datetime

from e2e.swarm.colony.window import window_drops

from hivemind.swarm.gateway import TransportRefusals
from hivemind.swarm.gateway.refusals import RECONNECT_GRACE_S, REPLAY_REFUSED_KIND
from waggle.clock import FakeClock
from waggle.ids import HiveId, NodeId
from waggle.transport.limits import ConnectionRefusal, RefusalKind

_HIVE = HiveId("hive_test")
_NODE = NodeId("node_test")


class _Recorder:
    """Stands in for the swarm recorder: keeps the guard events it is asked to write."""

    def __init__(self) -> None:
        self.written: list[tuple[str, str]] = []

    def guard_event(self, kind: str, subject: str, payload: dict[str, object]) -> tuple[str, str]:
        """Make the event the real recorder would, as the kind and the subject it names."""
        return kind, subject

    async def record(self, event: tuple[str, str]) -> None:
        """Write the event to the stand-in trail."""
        self.written.append(event)


class _Hub:
    """Stands in for the gateway hub: says when the node's link attached."""

    def __init__(self, attached: datetime) -> None:
        self._attached = attached

    def attached_at_for_node(self, node_id: NodeId) -> datetime:
        """When the link attached, which the grace is counted from."""
        return self._attached


def _refusals(clock: FakeClock, recorder: _Recorder) -> TransportRefusals:
    """The real callback, its link attached at the clock's present."""
    return TransportRefusals(recorder, _Hub(clock.now()), _HIVE)  # type: ignore[arg-type]


def _duplicate(clock: FakeClock) -> ConnectionRefusal:
    """A repeated envelope id from the node, seen now."""
    return ConnectionRefusal(_NODE, RefusalKind.REPLAY, "msg_repeated", clock.now())


async def test_a_duplicate_inside_the_grace_is_heard_though_nothing_is_recorded() -> None:
    clock, recorder = FakeClock(), _Recorder()
    refusals = _refusals(clock, recorder)
    duplicate = _duplicate(clock)

    with window_drops() as heard:
        await refusals(duplicate)

    assert heard == [duplicate]
    assert recorder.written == []  # The grace is why the trail says nothing, and the tap does.


async def test_a_duplicate_after_the_grace_is_heard_and_recorded_as_it_always_was() -> None:
    clock, recorder = FakeClock(), _Recorder()
    refusals = _refusals(clock, recorder)
    clock.advance(RECONNECT_GRACE_S + 1.0)
    duplicate = _duplicate(clock)

    with window_drops() as heard:
        await refusals(duplicate)

    assert heard == [duplicate]
    assert recorder.written == [(REPLAY_REFUSED_KIND, _NODE)]


async def test_the_callback_is_the_real_one_again_when_the_block_ends() -> None:
    clock, recorder = FakeClock(), _Recorder()
    refusals = _refusals(clock, recorder)
    clock.advance(RECONNECT_GRACE_S + 1.0)
    real = TransportRefusals.__call__

    with window_drops() as heard:
        assert TransportRefusals.__call__ is not real
    await refusals(_duplicate(clock))

    assert TransportRefusals.__call__ is real
    assert heard == []  # Heard only while the block lasted.
    assert len(recorder.written) == 1  # And the real callback went on recording.
