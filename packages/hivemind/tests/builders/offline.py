"""Build the messages, clock and scripted Queen the offline Warden tests share (roadmap 11.9).

A reconnecting link is tested from both ends: the Warden side sends the messages a Warden sends
(a result, a checkpoint, an Alarm, a question...) and the Queen side is a script that hands out
connections, reads what arrives and drops them on cue. `Wire` wraps each message kind a Warden
sends to the Queen in an envelope addressed the way the Warden addresses it. `AutoClock` is a
FakeClock whose sleeps pass at once and are recorded, so a reconnection's whole backoff schedule
runs in no time and the test reads the schedule back. `ScriptedQueen` is a `QueenDialer`: it hands
out in-memory connections, collects every frame the Warden puts on them, fails the next few dials
when asked, and drops or closes the current connection on cue. It can also pull the cable the way
a real one is pulled: `silence` makes the connection take frames and hear none of them, with no
error anywhere, until `drop` is the keepalive giving up. `RecordingAnnouncer` and
`RecordingAfter` are the announce and after-reconnect steps that remember how they were called.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the tests under
    packages/hivemind/tests/unit/wardens/offline.

Key invariants:
    - Every message built here is a real, validated model; nothing bypasses validation.
    - `ScriptedQueen` reaps every task it started in `aclose`, so no test leaves one pending.
    - `AutoClock.sleep` never waits: it moves the clock by the requested time and yields once.

See Also:
    - hivemind.wardens.offline for the link, outbox and policy these builders exercise.
    - builders.wardens for the QueenEnd used where the Warden itself is under test.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from builders.honey import make_nectar_deposit

from hivemind.common.tasks import reap, reap_all
from hivemind.wardens.offline import (
    LinkPhase,
    QueenDialer,
    ReconnectingQueenLink,
    Reconnection,
    open_outbox,
)
from waggle.clock import Clock, FakeClock
from waggle.codec import Codec
from waggle.envelope import Envelope, Hop, wrap
from waggle.errors import ConnectFailedError, ConnectionLostError, TransportError
from waggle.ids import (
    TaskId,
    new_alarm_id,
    new_cell_id,
    new_event_id,
    new_grant_id,
    new_hive_id,
    new_lease_id,
    new_message_id,
    new_node_id,
    new_task_id,
    new_warden_id,
    new_worker_id,
)
from waggle.messages import AlarmSeverity
from waggle.messages.base import WaggleMessage
from waggle.messages.cell import CellRequest, LeaseReleased, ReleaseCause
from waggle.messages.cell.status import CellHeartbeat, CellMode
from waggle.messages.forage import ForageDelta, ForageRequest, ForageRequestKind
from waggle.messages.labels import (
    AccessLevel,
    AccuracyBar,
    HandoffRef,
    HoneyClearance,
    Tempo,
)
from waggle.messages.supervision import (
    AlarmContext,
    AlarmKind,
    AlarmRaised,
    ContextTelemetry,
    Heartbeat,
    Question,
    WardenState,
)
from waggle.messages.swarm import TrailSegmentSync
from waggle.messages.task import TaskOutcome, TaskProgress, TaskResult, TaskStage
from waggle.transport.base import Transport
from waggle.transport.memory import MemoryTransport
from waggle.transport.recent import RecentFrames

WAIT_S = 5.0  # Bounds every wait on the link; a loopback or in-memory recovery takes milliseconds.

__all__ = [
    "WAIT_S",
    "AutoClock",
    "LinkRig",
    "RecordingAfter",
    "RecordingAnnouncer",
    "ScriptedQueen",
    "Wire",
    "make_wire",
]


class AutoClock(FakeClock):
    """A FakeClock whose sleeps pass at once: time jumps by the sleep, and nothing waits."""

    def __init__(self) -> None:
        """Create an AutoClock that has slept for nothing yet."""
        super().__init__()
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        """Record `seconds`, move the clock by it, and yield once so other tasks run."""
        self.sleeps.append(seconds)
        if seconds > 0:
            self.advance(seconds)
        await asyncio.sleep(0)


@dataclass(frozen=True)
class Wire:
    """Wraps the messages a Warden sends the Queen, addressed the way the Warden addresses them."""

    clock: Clock
    hop: Hop
    task_id: TaskId

    def wrap(self, payload: WaggleMessage) -> Envelope:
        """Wrap `payload` from the Warden to the Queen."""
        return wrap(payload, self.hop, clock=self.clock)

    def result(self, outcome: TaskOutcome = TaskOutcome.SUCCEEDED) -> Envelope:
        """A verified `task.result` for this wire's task."""
        return self.wrap(
            TaskResult(
                task_id=self.task_id,
                attempt=1,
                outcome=outcome,
                summary="The work is done.",
                clearance=HoneyClearance.C1,
                artifacts=(),
                checked_by=self.hop.sender,
                handoff=None,
                spend=0.0,
                reason="Every acceptance criterion held.",
            )
        )

    def progress(self, stage: TaskStage = TaskStage.CHECKPOINTED) -> Envelope:
        """A `task.progress` at `stage`, with a Handoff reference exactly when it checkpoints."""
        ref = HandoffRef(
            event_id=new_event_id(self.clock),
            written_at=self.clock.now(),
            clearance=HoneyClearance.C1,
        )
        return self.wrap(
            TaskProgress(
                task_id=self.task_id,
                attempt=1,
                stage=stage,
                summary="A step was taken.",
                clearance=HoneyClearance.C1,
                fraction_done=None,
                handoff=ref if stage is TaskStage.CHECKPOINTED else None,
            )
        )

    def alarm(self) -> Envelope:
        """A `supervision.alarm_raised` for this wire's task."""
        return self.wrap(
            AlarmRaised(
                alarm_id=new_alarm_id(self.clock),
                kind=AlarmKind.WORKER_CRASHED,
                severity=AlarmSeverity.WARNING,
                origin=self.hop.sender,
                attempts=0,
                raised_at=self.clock.now(),
                context=AlarmContext(
                    task_id=self.task_id, cell_id=None, worker_id=None, event_id=None, handoff=None
                ),
                detail="A sub-bee stopped.",
                clearance=HoneyClearance.C1,
                reason="The Warden could not mend it.",
            )
        )

    def forage_request(self) -> Envelope:
        """A `forage.request` for one more shared seat (a REQUEST: a reply is owed)."""
        return self.wrap(
            ForageRequest(
                grant_id=new_grant_id(self.clock),
                kind=ForageRequestKind.SHARED_SEATS,
                wanted=ForageDelta(source_id=None, slot=None, minimum_grade=None, seats=1),
                task_id=self.task_id,
                tempo=Tempo(latency_budget_s=None, accuracy=AccuracyBar.NORMAL),
                reason="The task needs a stronger source.",
            )
        )

    def question(self) -> Envelope:
        """A human-bound `supervision.question` (a REQUEST)."""
        return self.wrap(
            Question(
                question_id=new_message_id(self.clock),
                task_id=self.task_id,
                asked_by=new_worker_id(self.clock),
                text="Which of these should I use?",
                options=(),
                clearance=HoneyClearance.C1,
                asked_at=self.clock.now(),
            )
        )

    def cell_request(self) -> Envelope:
        """A `cell.request` for a new Cell (a REQUEST)."""
        return self.wrap(
            CellRequest(
                cell_id=new_cell_id(self.clock),
                needs=None,
                holder=self.hop.sender,
                task_id=self.task_id,
                access_level=AccessLevel.SCRATCH,
                lifetime_s=None,
                reason="The task needs another machine.",
            )
        )

    def heartbeat(self) -> Envelope:
        """The Warden's own `supervision.heartbeat`."""
        telemetry = ContextTelemetry(
            tokens_used=0,
            context_window=1000,
            goal="Supervising.",
            last_actions=(),
            blockers=(),
            spend=0.0,
        )
        return self.wrap(
            Heartbeat(
                telemetry=telemetry,
                task_id=None,
                worker_state=None,
                warden_state=WardenState.ACTIVE,
                children=(),
                grant_id=None,
                grant_spend=None,
                interval_s=5.0,
            )
        )

    def lease_released(self) -> Envelope:
        """A `cell.lease_released` (an event the Queen must learn of)."""
        return self.wrap(
            LeaseReleased(
                lease_id=new_lease_id(self.clock),
                cell_id=new_cell_id(self.clock),
                holder=self.hop.sender,
                cause=ReleaseCause.COMPLETED,
                is_restored=True,
                killed_processes=0,
                residual_paths=(),
                reason="The task is over.",
            )
        )

    def nectar(self, **fields: object) -> Envelope:
        """A `honey.nectar_deposit`: one chunk of findings, an event the Queen must learn of.

        Args:
            **fields: Fields of the deposit to replace, such as the Cell it was gathered on.
        """
        return self.wrap(make_nectar_deposit(self.clock, **fields))

    def trail_segment(self) -> Envelope:
        """A one-chunk `swarm.trail_segment_sync`, the message a reconnection ships."""
        body = b"{}"
        first = new_event_id(self.clock)
        return self.wrap(
            TrailSegmentSync(
                node_id=self.hop.node_id,
                cell_id=new_cell_id(self.clock),
                warden_id=self.hop.sender,
                from_at=self.clock.now(),
                to_at=self.clock.now(),
                first_event_id=first,
                last_event_id=first,
                event_count=1,
                segment_format_version=1,
                chunk=body,
                offset=0,
                total_bytes=len(body),
                final=True,
                sha256=hashlib.sha256(body).hexdigest(),
                segment_signature=None,
            )
        )

    def cell_heartbeat(self) -> Envelope:
        """A `cell.heartbeat`."""
        return self.wrap(
            CellHeartbeat(
                cell_id=new_cell_id(self.clock),
                mode=CellMode.ACTIVE,
                lease_ids=(),
                worker_count=0,
                is_shield_verified=False,
                interval_s=5.0,
            )
        )


def make_wire(clock: Clock | None = None) -> Wire:
    """Build a `Wire` for a fresh Warden, Queen and task on `clock` (a new FakeClock if omitted)."""
    active = clock if clock is not None else FakeClock()
    hop = Hop(
        sender=new_warden_id(active), recipient=new_hive_id(active), node_id=new_node_id(active)
    )
    return Wire(active, hop, new_task_id(active))


@dataclass
class ScriptedQueen:
    """The Queen's end of a reconnecting link: hands out connections and keeps what arrives.

    Owns mutable state in place (codingrules 8.5): the connections handed out, the frames read off
    them and the dials still to fail grow and shrink as a test runs.
    """

    fail_dials: int = 0  # The next this-many dials fail with a connect error.
    dials: int = 0
    ends: list[MemoryTransport] = field(default_factory=list)  # The Queen's end of each connection.
    warden_ends: list[MemoryTransport] = field(default_factory=list)  # And the Warden's.
    received: list[Envelope] = field(default_factory=list)
    swallowed: list[Envelope] = field(default_factory=list)  # Taken while silenced, heard by none.
    _readers: list[asyncio.Task[None]] = field(default_factory=list)
    _gate: asyncio.Event = field(default_factory=asyncio.Event)
    _silenced: bool = False
    # Set on every dial and every frame received, so a test waiting on either wakes at once.
    activity: asyncio.Event = field(default_factory=asyncio.Event)

    def __post_init__(self) -> None:
        """Start with the Queen reachable: dials are not held back."""
        self._gate.set()

    def block_dials(self) -> None:
        """Hold every dial until `release_dials`: the Queen is out of reach, not refusing."""
        self._gate.clear()

    def release_dials(self) -> None:
        """Let the dials that were held back, and later ones, through."""
        self._gate.set()

    async def dial(self) -> Transport:
        """Hand out a fresh connection, or fail as an unreachable Queen does."""
        self.dials += 1
        self.activity.set()
        await self._gate.wait()
        if self.fail_dials > 0:
            self.fail_dials -= 1
            raise ConnectFailedError("The scripted Queen is unreachable.")
        queen_end, warden_end = MemoryTransport.pair(Codec(), Codec())
        self.ends.append(queen_end)
        self.warden_ends.append(warden_end)
        self._readers.append(asyncio.ensure_future(self._collect(queen_end)))
        return warden_end

    @property
    def kinds(self) -> list[str]:
        """The kind of every frame received, in arrival order."""
        return [envelope.kind for envelope in self.received]

    def silence(self) -> None:
        """Pull the cable and tell nobody: sends return, frames arrive nowhere, nothing raises."""
        self._silenced = True

    def drop(self) -> None:
        """Cut the current connection the way a network does: both ends raise.

        After a `silence` this is the keepalive giving up on the dead link, forty seconds later.
        """
        self._silenced = False
        self.ends[-1].drop()

    async def close_current(self) -> None:
        """Close the current connection cleanly, as a restarting Queen does."""
        await self.ends[-1].close()

    async def send(self, envelope: Envelope) -> None:
        """Send `envelope` to the Warden on the current connection."""
        await self.ends[-1].send(envelope)

    async def aclose(self) -> None:
        """Close every connection and reap the tasks reading them."""
        for end in self.ends:
            await end.close()
        await reap_all(self._readers)

    async def _collect(self, end: MemoryTransport) -> None:
        """Read `end` until it ends or drops, keeping every frame in arrival order."""
        try:
            async for envelope in end.receive():
                if self._silenced:
                    self.swallowed.append(envelope)  # Returned as sent, and heard by nobody.
                else:
                    self.received.append(envelope)
                self.activity.set()
        except TransportError:
            return  # The connection was dropped: what had arrived is already kept.


class RecordingAnnouncer:
    """An announcer that sends one marker frame per connection and counts its calls."""

    def __init__(self, wire: Wire) -> None:
        """Build the announcer over `wire`, whose `cell.heartbeat` is the marker."""
        self._wire = wire
        self.calls = 0

    async def announce(self, transport: Transport) -> None:
        """Send the marker on `transport`."""
        self.calls += 1
        await transport.send(self._wire.cell_heartbeat())


class RecordingAfter:
    """An after-reconnect step that remembers each outage it was told about, and can fail."""

    def __init__(self) -> None:
        """Build the step with no outage seen yet and no failure scripted."""
        self.outages: list[float] = []
        self.fail_times = 0  # The next this-many calls fail as a link that died mid-sequence.

    async def reconnected(self, outage_s: float) -> None:
        """Remember `outage_s`, unless the link is scripted to die during this step."""
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ConnectionLostError("The new link died while the trail was shipping.")
        self.outages.append(outage_s)


class LinkRig:
    """A `ReconnectingQueenLink` over a `ScriptedQueen`, with the pump a Warden's receive task is.

    The link recovers only while something iterates its `receive`, as the Warden's own receive
    task does, so `start` runs that pump and keeps every frame the Queen sends. Every phase the
    link reports is kept in `phases`; `until` waits, driven by those reports and bounded in time.
    """

    def __init__(
        self,
        directory: Path,
        queen: ScriptedQueen | None = None,
        dialer: QueenDialer | None = None,
        clock: AutoClock | None = None,
        recent: RecentFrames | None = None,
    ) -> None:
        """Build the link over a fresh queue under `directory`; nothing is dialled until `start`.

        Args:
            directory: Where the link's durable outbox lives.
            queen: The scripted Queen to dial; a fresh one when omitted.
            dialer: Dials something else (a real socket) when given; `queen` then only serves as
                the wake-up the test waits on, which whatever `dialer` talks to may also set.
            clock: The clock the link backs off on; a fresh `AutoClock` when omitted.
            recent: The link's memory of what it sent live; the default ring when omitted.
        """
        self.clock = clock if clock is not None else AutoClock()
        self.wire = make_wire(self.clock)
        self.queen = queen if queen is not None else ScriptedQueen()
        self.changed = asyncio.Event()  # One wake-up for phases, dials and frames alike.
        self.queen.activity = self.changed
        self.announcer = RecordingAnnouncer(self.wire)
        self.after = RecordingAfter()
        self.outbox = open_outbox(directory, Codec(), self.clock)
        parts = Reconnection(
            dialer if dialer is not None else self.queen,
            self.announcer,
            self.after,
            self.clock,
            recent if recent is not None else RecentFrames(),
        )
        self.link = ReconnectingQueenLink(parts, self.outbox)
        self.phases: list[LinkPhase] = []
        self.heard: list[Envelope] = []
        self._pump: asyncio.Task[None] | None = None
        self.link.watch(self._on_change)

    def start(self) -> None:
        """Start the pump: the first connection is made by it, as the Warden's loop makes it."""
        self._pump = asyncio.ensure_future(self._run())

    @property
    def pump_is_running(self) -> bool:
        """Whether the pump's `receive` is still going (it must not end before `close`)."""
        return self._pump is not None and not self._pump.done()

    async def until(self, ready: Callable[[], bool]) -> None:
        """Wait until `ready()` holds, re-checking at every phase, dial or frame, for WAIT_S."""
        async with asyncio.timeout(WAIT_S):
            while not ready():
                self.changed.clear()
                await self.changed.wait()

    async def pump_ended(self) -> None:
        """Wait, for at most WAIT_S, for the pump's `receive` to end by itself."""
        assert self._pump is not None
        async with asyncio.timeout(WAIT_S):
            await self._pump

    async def stop(self) -> None:
        """Close the link, end the pump and the Queen's side, leaving no task pending."""
        await self.link.close()
        # Ends by itself once the link is closed, unless it is blocked on a dial: a Warden's stop
        # cancels its receive task for exactly that reason, so this does too.
        if self._pump is not None:
            await reap(self._pump)
        await self.queen.aclose()

    async def _run(self) -> None:
        """Iterate the link's `receive` until it ends, keeping every frame."""
        async for envelope in self.link.receive():
            self.heard.append(envelope)
            self.changed.set()

    def _on_change(self) -> None:
        """Remember the new phase and wake whoever waits in `until`."""
        self.phases.append(self.link.phase)
        self.changed.set()
