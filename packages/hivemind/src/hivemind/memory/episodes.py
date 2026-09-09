"""Define EpisodeRecord and EpisodeStream: memory's record of a bee's thinking, and its live feed.

Roadmap step 3.14: "an EpisodeRecord for every awake episode and every autopilot decision: who,
trigger, the assembled prompt by reference, the provider's reasoning summary where it exposes one,
the decision, the action taken; stored in Bee Bread with a retention window, never on the trail,
and streamable live so the Observation Hive can show any bee's thinking as it happens." Codingrules
section 12: "Thoughts are memory, not audit" -- the trail records only that `memory.episode`
happened and a couple of counts (`hivemind.memory.record_episode`), never the reasoning itself.
`EpisodeStream` is the live half: a bounded, in-process asyncio publish/subscribe broadcaster the
Observation Hive's thinking view subscribes to, built the same way `hivemind.pheromone.trail.tail.
follow` streams the Pheromone Trail, but as a direct push rather than a poll (episodes are not
persisted-then-tailed the way trail events are; a subscriber that was not listening when a record
was published simply never sees it, which is fine for a live view).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). `record_episode` is called by
    queen.awake, wardens.awake and every autopilot dispatch (later roadmap steps); `EpisodeStream`
    is constructed once per process and shared between whatever publishes episodes and the
    Observation Hive route that subscribes. Calls into hivemind.cell (HoneyClearance),
    hivemind.forage (ModelSlot), hivemind.memory.context (MemoryContext), hivemind.memory.
    hot_state (TriggerEvent), hivemind.pheromone (LlmUsage, MemoryEvent) and waggle only.

Key invariants:
    - EpisodeRecord.id is an EventId-shaped ULID minted by `waggle.ids.new_event_id`; no dedicated
      `EpisodeId` IdKind exists this phase.
    - EpisodeStream.publish never blocks on a slow subscriber: a full per-subscriber queue drops
      its own oldest buffered record to make room for the new one (`_offer`), so one slow
      Observation Hive viewer can never stall the bee whose thinking is being recorded.
    - EpisodeStream.subscribe never returns on its own and never swallows cancellation: the only
      way to stop iterating it is to cancel the task consuming it, exactly like
      `hivemind.pheromone.trail.tail.follow`.

See Also:
    - .claude/codingrules.md section 12 for "Thoughts are memory, not audit".
    - .claude/roadmap.md step 3.14 for the EpisodeRecord fields this module implements.
    - hivemind.pheromone.trail.tail for follow, the polling counterpart this module's streaming
      shape is modelled on.
    - hivemind.memory.hot_state for TriggerEvent, the shape EpisodeRecord.trigger carries.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import HoneyClearance
from hivemind.forage.slots import ModelSlot
from hivemind.memory.context import MemoryContext
from hivemind.memory.hot_state import TriggerEvent
from hivemind.pheromone import LlmUsage, MemoryEvent
from waggle.ids import new_event_id
from waggle.messages.base import EventIdField, UtcDatetime

MAX_PRINCIPAL_CHARS = 128  # A bee id or role name, matching hot_state.Principal.id's own cap.
MAX_PROMPT_REF_CHARS = 200  # A reference (an id or a short key), never the prompt text itself.
MAX_REASONING_SUMMARY_CHARS = 4_000  # A provider's reasoning summary, never a full transcript.
MAX_DECISION_CHARS = 2_000  # What was decided, in a paragraph or two.
MAX_ACTION_CHARS = 2_000  # What was done about it, likewise.
# Bounded per-subscriber buffer: generous enough to absorb a burst of episodes between reads, but
# never unbounded, so a stalled Observation Hive viewer costs a fixed amount of memory, not more.
DEFAULT_QUEUE_SIZE = 100

__all__ = [
    "DEFAULT_QUEUE_SIZE",
    "MAX_ACTION_CHARS",
    "MAX_DECISION_CHARS",
    "MAX_PRINCIPAL_CHARS",
    "MAX_PROMPT_REF_CHARS",
    "MAX_REASONING_SUMMARY_CHARS",
    "EpisodeRecord",
    "EpisodeStream",
    "record_episode",
]


class EpisodeRecord(BaseModel):
    """One awake episode's or one autopilot decision's record: who, trigger, decision, action.

    Stateless by construction (codingrules section 8.8: "Awake episodes are stateless"): this is
    the whole record of one episode, never accumulated with any other.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EventIdField = Field(
        description="This episode's own id: an EventId-shaped ULID; no EpisodeId kind this phase."
    )
    principal: str = Field(
        max_length=MAX_PRINCIPAL_CHARS, description="Who decided: a bee id or role."
    )
    slot: ModelSlot = Field(description="The ModelSlot this episode ran on.")
    trigger: TriggerEvent = Field(description="What triggered this episode.")
    prompt_ref: str | None = Field(
        default=None,
        max_length=MAX_PROMPT_REF_CHARS,
        description="A reference to the assembled prompt, never the prompt text itself.",
    )
    reasoning_summary: str | None = Field(
        default=None,
        max_length=MAX_REASONING_SUMMARY_CHARS,
        description="The provider's own reasoning summary, when it exposes one.",
    )
    decision: str = Field(max_length=MAX_DECISION_CHARS, description="What was decided.")
    action: str = Field(max_length=MAX_ACTION_CHARS, description="What was done about it.")
    at: UtcDatetime = Field(description="When this episode ran.")
    clearance: HoneyClearance = Field(description="This episode's data-sensitivity label.")
    usage: LlmUsage | None = Field(
        default=None, description="Normalised token and cost usage; None for an autopilot decision."
    )
    is_autopilot: bool = Field(
        description="True for a deterministic autopilot dispatch; False for an awake episode."
    )


async def record_episode(record: EpisodeRecord, ctx: MemoryContext) -> None:
    """Write `record` and its `memory.episode` trail event, atomically.

    Args:
        record: The episode to record; its id must be new to the store.
        ctx: The store, identity and clock to write with.

    Returns:
        None, once the record and its event are durably recorded together. Publishing the same
        record on an `EpisodeStream` for live viewers is a separate, explicit step this function
        does not take.
    """
    event = MemoryEvent(
        id=new_event_id(ctx.clock),
        hive_id=ctx.identity.hive_id,
        node_id=ctx.identity.node_id,
        at=ctx.clock.now(),
        actor=ctx.identity.actor,
        kind="memory.episode",
        subject_id=record.id,
        payload={"is_autopilot": record.is_autopilot, "has_usage": record.usage is not None},
    )
    await ctx.store.put_episode(record, event)


class EpisodeStream:
    """A bounded, in-process publish/subscribe broadcaster of live EpisodeRecords.

    Every subscriber gets its own bounded queue; `publish` offers a record to each one without
    ever awaiting on a slow consumer (`_offer` drops the oldest buffered record instead).
    """

    def __init__(self, queue_size: int = DEFAULT_QUEUE_SIZE) -> None:
        """Create a stream with no subscribers yet.

        Args:
            queue_size: Maximum records buffered per subscriber before the oldest is dropped.
        """
        self._queue_size = queue_size
        self._subscribers: set[asyncio.Queue[EpisodeRecord]] = set()
        # Guards _subscribers only; publish() takes a snapshot under the lock and then offers to
        # each queue outside it, so registering or unregistering a subscriber never waits on a
        # slow put (there isn't one -- see _offer, which never blocks).
        self._lock = asyncio.Lock()

    async def subscribe(self) -> AsyncIterator[EpisodeRecord]:
        """Yield every EpisodeRecord published from this point on, until cancelled.

        Never returns on its own: the only way to stop it is to cancel the task iterating it,
        exactly like `hivemind.pheromone.trail.tail.follow`.

        Yields:
            Each EpisodeRecord published while this subscription is open, in publish order.
        """
        queue: asyncio.Queue[EpisodeRecord] = asyncio.Queue(maxsize=self._queue_size)
        async with self._lock:
            self._subscribers.add(queue)
        try:
            while True:
                # External wait: resolves as soon as publish() offers a record to this queue; an
                # uncaught cancellation here propagates straight through the finally below.
                yield await queue.get()
        finally:
            async with self._lock:
                self._subscribers.discard(queue)

    async def publish(self, record: EpisodeRecord) -> None:
        """Offer `record` to every current subscriber.

        Args:
            record: The episode to broadcast.

        Returns:
            None, once every current subscriber's queue has been offered the record (a full queue
            drops its own oldest item first; this method never waits for a subscriber to drain).
        """
        async with self._lock:
            # Snapshot while holding the lock; offering to each queue below never touches
            # `_subscribers` itself, so it is safe to do outside the lock.
            subscribers = tuple(self._subscribers)
        for queue in subscribers:
            _offer(queue, record)


def _offer(queue: asyncio.Queue[EpisodeRecord], record: EpisodeRecord) -> None:
    """Put `record` on `queue`, evicting its oldest item first if it is already full."""
    try:
        queue.put_nowait(record)
    except asyncio.QueueFull:
        # A slow subscriber never blocks the publisher: drop the oldest buffered record to make
        # room, so the live view always shows the most recent thinking instead of stalling on a
        # backlog it may never catch up on.
        with contextlib.suppress(asyncio.QueueEmpty):
            queue.get_nowait()
        queue.put_nowait(record)
