"""Build the Honey Store's Waggle side for tests: its access bundle, raw link ends, queries, seeds.

Roadmap step 7.8 puts the Honey Store (the Hive's knowledge base) on the wire: a Worker's
`HoneyQuery` and `NectarDeposit` chunks travel through its Warden to the Queen, and her
`HoneyResponse` or `control.error` comes back. The existing Queen/Warden/Worker builders sort
only the kinds their own tests need, so this module adds what the honey tests share:
`make_honey_access` (a `HoneyAccess` over a real store with no model bound: full-text search and
heuristic summaries, exactly what a Hive with no embedder or ripener runs), `WireEnd` (one raw
end of a `MemoryTransport` pair a test drives by hand), `make_honey_link` (a Queen-side
`WardenLink` for a given Cell plus the Warden's `WireEnd`), `make_honey_query`,
`make_deposit_meta`, `make_honey_hit`, `seed_finding` (a finding taken in and ripened, so a
query has Honey to find), and `FakeHoneyChannel` (a `hivemind.workers.context.HoneyChannel`
that records what a tool or the runtime sends and answers from a script, as
`builders.workers.FakeAsker` does for `QuestionChannel`).

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the unit tests of
    hivemind.queen.ticks.honey, hivemind.wardens.ticks.honey, hivemind.workers.runtime.honey and
    hivemind.workers.tools.honey, the Warden's autopilot table and dispatch tests, and
    tests/e2e/test_honey_wire.py.

Key invariants:
    - Every builder that mints an id or a timestamp takes a `clock: Clock`, so a run is
      deterministic.
    - `WireEnd.next` never waits forever: it raises `TimeoutError` after `RECEIVE_TIMEOUT_S`.

See Also:
    - builders.honey for the store-level builders this one builds on.
    - hivemind.honey_store.access for HoneyAccess, what `make_honey_access` returns.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections import deque
from collections.abc import Sequence

from builders.honey import make_honey_identity, make_nectar_submission

from hivemind.cell import Cell, HoneyClearance
from hivemind.honey_store import (
    HoneyAccess,
    HoneyRetriever,
    HoneyStore,
    Nectar,
    NectarIntake,
    RetrieverDeps,
    Ripener,
    RipenerDeps,
)
from hivemind.manifest import (
    HoneyClearanceSection,
    HoneyRetrievalSection,
    HoneyRipeningSection,
    HoneyStoreSection,
)
from hivemind.queen.deps import WardenLink
from hivemind.workers.nectar import DepositMeta
from waggle.clock import Clock
from waggle.codec import Codec
from waggle.envelope import Envelope, Hop, wrap
from waggle.ids import (
    HiveId,
    MessageId,
    NodeId,
    new_cell_id,
    new_task_id,
    new_warden_id,
    new_worker_id,
)
from waggle.messages import CombShieldLevel as WireCombShieldLevel
from waggle.messages import HoneyClearance as WireHoneyClearance
from waggle.messages.base import WaggleMessage
from waggle.messages.honey import (
    HoneyHit,
    HoneyProvenance,
    HoneyQuery,
    HoneyResponse,
    NectarDeposit,
    NectarKind,
)
from waggle.transport.memory import MemoryTransport

RECEIVE_TIMEOUT_S = 5.0  # A real-time guard only: an in-process reply arrives in microseconds.
FINDING_TEXT = (
    "The widget factory's staging config lives at /etc/widgets/staging.toml and is reloaded "
    "by sending SIGHUP to the widgetd process."
)  # What `seed_finding` stores by default; `FINDING_QUERY` finds it by full text.
FINDING_QUERY = "widget staging config"

__all__ = [
    "FINDING_QUERY",
    "FINDING_TEXT",
    "RECEIVE_TIMEOUT_S",
    "FakeHoneyChannel",
    "WireEnd",
    "make_deposit_meta",
    "make_honey_access",
    "make_honey_hit",
    "make_honey_link",
    "make_honey_query",
    "seed_finding",
]


class WireEnd:
    """One end of a MemoryTransport pair a test drives by hand: wrap, send, and read envelopes.

    Owns its own receive iterator (codingrules section 8.5), so every `next` reads the same
    stream in order.
    """

    def __init__(self, transport: MemoryTransport, hop: Hop, clock: Clock) -> None:
        """Wrap `transport`, sending as `hop`.

        Args:
            transport: This end of the pair.
            hop: This end's own address (`sender`) and its peer's (`recipient`).
            clock: Source of every envelope id and timestamp this end wraps.
        """
        self.transport = transport
        self.hop = hop
        self._clock = clock
        self._inbox = transport.receive()

    def envelope_for(
        self, payload: WaggleMessage, *, correlation_id: MessageId | None = None
    ) -> Envelope:
        """Wrap `payload` as this end would send it, without sending it.

        Args:
            payload: The message.
            correlation_id: The request it answers, when its kind needs one.

        Returns:
            The envelope.
        """
        return wrap(payload, self.hop, clock=self._clock, correlation_id=correlation_id)

    async def send(
        self, payload: WaggleMessage, *, correlation_id: MessageId | None = None
    ) -> Envelope:
        """Wrap and send `payload`, returning the envelope so a test knows its id.

        Args:
            payload: The message.
            correlation_id: The request it answers, when its kind needs one.

        Returns:
            The envelope sent.
        """
        envelope = self.envelope_for(payload, correlation_id=correlation_id)
        await self.transport.send(envelope)
        return envelope

    async def next(self, timeout_s: float = RECEIVE_TIMEOUT_S) -> Envelope:
        """Return the next envelope the peer sent.

        Args:
            timeout_s: A real-time guard so a missing reply fails the test instead of hanging.

        Returns:
            The envelope.

        Raises:
            TimeoutError: Nothing arrived within `timeout_s`.
            StopAsyncIteration: The pair ended cleanly with nothing more queued.
        """
        async with asyncio.timeout(timeout_s):
            return await anext(self._inbox)


class FakeHoneyChannel:
    """A HoneyChannel that records every query and deposit and answers from a scripted queue.

    Owns its own mutable state in place (codingrules section 8.5): `queries` and `deposits` grow
    with every call.
    """

    def __init__(self, fail_with: Exception | None = None) -> None:
        """Create a channel with nothing scripted.

        Args:
            fail_with: Raised by every `deposit` when set, to stand in for a lost link.
        """
        self.queries: list[HoneyQuery] = []
        self.deposits: list[tuple[NectarDeposit, ...]] = []
        self._responses: deque[HoneyResponse] = deque()
        self._fail_with = fail_with

    def script(self, *responses: HoneyResponse) -> None:
        """Queue `responses`, FIFO, one per future `query` call.

        Args:
            responses: The responses to return, in order.
        """
        self._responses.extend(responses)

    async def query(self, query: HoneyQuery) -> HoneyResponse:
        """Record `query` and pop the next scripted response.

        Args:
            query: The query asked.

        Returns:
            The next scripted response.

        Raises:
            IndexError: Nothing was scripted for this call.
        """
        self.queries.append(query)
        return self._responses.popleft()

    async def deposit(self, chunks: Sequence[NectarDeposit]) -> None:
        """Record one deposit's chunks, or raise `fail_with`.

        Args:
            chunks: The deposit's chunks, in order.

        Raises:
            Exception: `fail_with`, when it was given.
        """
        if self._fail_with is not None:
            raise self._fail_with
        self.deposits.append(tuple(chunks))


def make_honey_access(store: HoneyStore, clock: Clock, **overrides: object) -> HoneyAccess:
    """Build a HoneyAccess over `store` with default `[honey]` settings and no model bound.

    Args:
        store: The store every handle shares (normally `builders.honey.open_test_honey_store`).
        clock: Injected clock for every event and id the handles mint.
        **overrides: HoneyAccess fields that replace the defaults (a stub `intake`, a stricter
            `clearance`, ...).

    Returns:
        A HoneyAccess: full-text search only, heuristic summaries, default label C1.
    """
    identity = make_honey_identity(clock)
    clearance = HoneyClearanceSection()
    retrieval = HoneyRetrievalSection()
    ripening = HoneyRipeningSection()
    default_label = HoneyClearance.from_wire(clearance.default_label)
    base = HoneyAccess(
        store=store,
        intake=NectarIntake(store, identity, clock, HoneyStoreSection(), default_label),
        retriever=HoneyRetriever(RetrieverDeps(store, identity, clock, retrieval)),
        ripener=Ripener(RipenerDeps(store, identity, clock, ripening)),
        identity=identity,
        retrieval=retrieval,
        ripening=ripening,
        clearance=clearance,
    )
    return dataclasses.replace(base, **overrides)  # type: ignore[arg-type]  # test-only overrides


def make_honey_link(
    cell: Cell, clock: Clock, hive_id: HiveId, node_id: NodeId
) -> tuple[WardenLink, WireEnd]:
    """Build a Queen-side WardenLink for `cell` and the Warden's own raw end of it.

    Args:
        cell: The Cell the Warden supervises: the one the Queen trusts for a deposit's tier.
        clock: Source of the Warden's id and of every envelope the Warden end wraps.
        hive_id: The Queen's own address.
        node_id: The node both ends stamp.

    Returns:
        `(link, warden_end)`: attach `link` in the handler's `wardens` mapping; read the Queen's
        replies, and wrap the Warden's own messages, with `warden_end`.
    """
    warden_id = new_warden_id(clock)
    queen_transport, warden_transport = MemoryTransport.pair(Codec(), Codec())
    queen_hop = Hop(sender=hive_id, recipient=warden_id, node_id=node_id)
    link = WardenLink(warden_id=warden_id, cell=cell, transport=queen_transport, hop=queen_hop)
    warden_hop = Hop(sender=warden_id, recipient=hive_id, node_id=node_id)
    return link, WireEnd(warden_transport, warden_hop, clock)


def make_honey_query(clock: Clock, **overrides: object) -> HoneyQuery:
    """Build a valid HoneyQuery from a fresh Worker for a fresh task, at C1, for `FINDING_QUERY`.

    Args:
        clock: Source of the default Worker and task ids.
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated HoneyQuery.
    """
    fields: dict[str, object] = {
        "text": FINDING_QUERY,
        "requester": new_worker_id(clock),
        "scopes": (),
        "max_tokens": 2_000,
        "max_clearance": WireHoneyClearance.C1,
        "task_id": new_task_id(clock),
    }
    fields.update(overrides)
    return HoneyQuery(**fields)


def make_deposit_meta(clock: Clock, **overrides: object) -> DepositMeta:
    """Build a FINDING DepositMeta from a fresh Worker and task on a fresh MEADOW Cell, at C1.

    Args:
        clock: Source of the default ids and the observed-at time.
        **overrides: Field values that replace the defaults below.

    Returns:
        A DepositMeta.
    """
    fields: dict[str, object] = {
        "kind": NectarKind.FINDING,
        "media_type": "text/markdown",
        "title": "A deposited finding",
        "task_id": new_task_id(clock),
        "cell_id": new_cell_id(clock),
        "worker_id": new_worker_id(clock),
        "observed_at": clock.now(),
        "clearance": WireHoneyClearance.C1,
        "origin_tier": WireCombShieldLevel.MEADOW,
    }
    fields.update(overrides)
    return DepositMeta(**fields)  # type: ignore[arg-type]  # test-only overrides


def make_honey_hit(clock: Clock, **overrides: object) -> HoneyHit:
    """Build a valid C1 hit in the `hive` scope from a fresh task, Cell and Worker.

    Args:
        clock: Source of the provenance ids and observed-at time.
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated HoneyHit.
    """
    fields: dict[str, object] = {
        "honey_ref": "/hive/honey_01hittest0000000000000000",
        "title": "Where the staging config lives",
        "excerpt": FINDING_TEXT,
        "score": 0.8,
        "scope": "hive",
        "clearance": WireHoneyClearance.C1,
        "origin_tier": WireCombShieldLevel.MEADOW,
        "provenance": HoneyProvenance(
            task_id=new_task_id(clock),
            cell_id=new_cell_id(clock),
            bee=new_worker_id(clock),
            observed_at=clock.now(),
        ),
    }
    fields.update(overrides)
    return HoneyHit(**fields)


async def seed_finding(
    access: HoneyAccess, cell: Cell, clock: Clock, text: str = FINDING_TEXT
) -> Nectar:
    """Take a C1 finding from `cell` in and ripen it, so a query has Honey to find.

    Args:
        access: The Honey Store handles to write through.
        cell: The Cell the finding was gathered on; a borrowed (Real) Cell labels it C2.
        clock: Source of the submission's ids and times.
        text: The finding's text.

    Returns:
        The stored Nectar row (ripened by the time this returns).
    """
    submission = make_nectar_submission(
        clock,
        content=text.encode("utf-8"),
        cell_id=cell.id,
        from_borrowed_cell=cell.is_borrowed,
        tier=cell.comb_shield,
    )
    result = await access.intake.submit(submission)
    # One pass ripens it: no model is bound, so the summary is heuristic and no vector is made.
    await access.ripener.run_pass()
    return result.nectar
