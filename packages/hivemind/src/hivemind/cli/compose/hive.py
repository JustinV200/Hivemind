"""Compose a Hive from a manifest, run it, and run one goal: build_hive, run_hive, run_goal.

Roadmap step 3.21 (second half)'s own composition root: `build_hive` is the *only* place a loaded
`hivemind.manifest.HiveManifest` becomes a running Hive's every collaborator (codingrules section
13), built by delegating each conversion to `hivemind.cli.compose.deps` and the one Queen<->Warden
link to `hivemind.cli.compose.links`. `run_hive` is an `asynccontextmanager` that leases the Hive
Stand's one Cell, runs the Queen and the Warden as two background tasks for as long as the `async
with` block is open, and tears both down -- releasing the lease, "left as found" -- on exit.
`run_goal` submits one goal and polls until every one of its tasks reaches a terminal
`hivemind.brood_chamber.TaskStatus`, or `timeout_s` elapses, forwarding trail events of interest to
an optional `on_event` callback and syncing any answer `hive inbox answer` left in another process
(`hivemind.queen.sync_answers_from_chamber`) on every poll.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.compose`. Called by
    `hivemind.cli.run` (roadmap step 3.21) and by every test that drives the kernel end to end
    against a `hivemind.llm.FakeLLMProvider`. Calls into `hivemind.brood_chamber`, `hivemind.cell`,
    `hivemind.cli.compose.deps`, `.links`, `hivemind.cli.stores`, `hivemind.pheromone`,
    `hivemind.queen`, `hivemind.wardens` and waggle only.

Key invariants:
    - `build_hive` never touches the network: every provider it constructs is lazy
      (`hivemind.llm.registry.ProviderRegistry.provider`'s own rule), and its own one `asyncio.run`
      call (`hivemind.cell.local.HiveStandSource.cells`, to seed the Queen<->Warden link's Cell)
      only probes this host's own capacity.
    - `run_hive` always stops the Queen, stops the Warden (releasing its lease) and closes the
      Queen<->Warden link, in that order, whether its `async with` block exits cleanly or raises.
    - `run_goal` never blocks past `timeout_s`: `GoalReport.timed_out` is True whenever the goal's
      own tasks are not all terminal by then, and `succeeded` is False in that case regardless of
      how far the goal got.

See Also:
    - .claude/codingrules.md section 13 for "the composition root is the only place a HiveManifest
      is converted".
    - .claude/codingrules.md section 11 for the `asyncio.TaskGroup`/structured-concurrency rule
      `run_hive` follows.
    - .claude/roadmap.md step 3.22 scenario (a) for the three-haiku goal `run_goal` is built to
      finish end to end, the seam this dispatch's own report proves against a fake provider.
    - hivemind.cli.compose.deps for every manifest-to-deps builder `build_hive` composes.
    - hivemind.cli.compose.links for build_hive_links, the one Queen<->Warden wire this phase uses.
    - hivemind.queen.sync_answers_from_chamber for the cross-process answer handoff `run_goal`
      polls for on every iteration.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime

from hivemind.brood_chamber import Task, TaskFilter, TaskStatus, is_terminal
from hivemind.cell import HoneyClearance
from hivemind.cell.local import HiveStandSource
from hivemind.cli.compose.deps import (
    HiveParts,
    HiveStores,
    build_fanner,
    build_hive_stand_source,
    build_provider_registry,
    build_queen_deps,
    build_warden_deps,
    open_default_stores,
)
from hivemind.cli.compose.links import HiveLinks, build_hive_links
from hivemind.cli.stores import build_forage_map
from hivemind.forage import ForageMap
from hivemind.llm import Fanner, ProviderRegistry, Responder
from hivemind.manifest import HiveManifest
from hivemind.pheromone import LlmEvent, PheromoneEvent, TrailQuery
from hivemind.queen import Queen, WardenLink, sync_answers_from_chamber
from hivemind.wardens import Warden
from waggle.clock import Clock
from waggle.ids import TaskId

__all__ = ["GoalReport", "Hive", "build_hive", "run_goal", "run_hive"]

# run_goal's own polling cadence, on the injected Clock: short enough that a FakeClock-driven unit
# test (codingrules 14.5) finishes in a handful of iterations, gentle enough to be a real interval
# against a live SQLite file in production (matches fake_manifest's own short heartbeat interval).
_POLL_INTERVAL_S = 0.05


@dataclass(frozen=True, slots=True)
class Hive:
    """Everything `hive run` -- and every test exercising the same kernel -- need a handle on.

    Attributes:
        manifest: The loaded HiveManifest this Hive was composed from.
        stores: The trail, chamber and memory store this Hive shares one SQLite file for.
        registry: Every configured model provider, lazily constructed and cached.
        fanner: The seat meter every model call in this Hive passes through.
        source: The Hive Stand's own RealCellSource; its one Cell is leased once `run_hive` starts.
        warden: The Hive Stand's own Warden, built but not yet started.
        queen: The Queen, with `warden`'s own WardenLink already attached.
        warden_link: The Queen's own end of the Queen<->Warden link; `run_hive` closes it on exit.
        clock: The injected time source every collaborator above shares.
    """

    manifest: HiveManifest
    stores: HiveStores
    registry: ProviderRegistry
    fanner: Fanner
    source: HiveStandSource
    warden: Warden
    queen: Queen
    warden_link: WardenLink
    clock: Clock


@dataclass(frozen=True, slots=True)
class GoalReport:
    """What `run_goal` returns once a goal's own tasks are terminal, or `timeout_s` elapsed.

    Attributes:
        goal_id: The goal's own id (`hivemind.queen.Queen.submit_goal`'s return value: the first
            minted task's id).
        tasks: Every task under this goal, in `hivemind.brood_chamber.TaskFilter` order, as of the
            moment `run_goal` stopped polling.
        succeeded: True when every task is `TaskStatus.SUCCEEDED` and `timed_out` is False.
        spend_usd: Every `llm.call` occurrence's own cost recorded on the trail from submission
            onward (Hive-wide, not goal-scoped: no per-goal Forage ledger exists this phase, see
            `_spend_since`'s own docstring).
        elapsed_s: Wall time (on the injected Clock's own `monotonic()`) from submission to when
            polling stopped.
        timed_out: True when `timeout_s` elapsed before every task reached a terminal status.
    """

    goal_id: TaskId
    tasks: tuple[Task, ...]
    succeeded: bool
    spend_usd: float
    elapsed_s: float
    timed_out: bool


def build_hive(
    manifest: HiveManifest,
    *,
    environ: Mapping[str, str],
    clock: Clock,
    stores: HiveStores | None = None,
    responders: Mapping[str, Responder] | None = None,
) -> Hive:
    """Turn a loaded Hive Manifest into a running Hive's every collaborator, not yet started.

    The one place a HiveManifest is converted into deps (codingrules section 13): every subsystem
    below `cli` takes only the slice `hivemind.cli.compose.deps`'s builders carve from `manifest`
    here, never the manifest itself. Never awaits a model or opens a network connection: every
    provider `registry` may later construct is lazy, and the one `asyncio.run` call this makes
    (`_build_links`, below) only probes this host's own already-known capacity.

    Args:
        manifest: A HiveManifest loaded by `hivemind.manifest.load_manifest`.
        environ: The composition root's own environment mapping, read once for provider API keys.
        clock: Injected time source shared by every collaborator this builds.
        stores: Supplies the trail, chamber and memory store directly (an in-memory, `FakeClock`-
            timestamped `HiveStores` a test builds by hand); `open_default_stores(manifest)` (real
            SQLite, `[hive] db`) when omitted.
        responders: Installed on every `kind = "fake"` provider this Hive constructs; see
            `hivemind.cli.compose.deps.build_provider_registry`'s own docstring. `None` in
            production, where no provider is ever `kind = "fake"`.

    Returns:
        A Hive whose Warden has not yet leased its Cell and whose Queen has not yet ticked; pass
        it to `run_hive` to start both.
    """
    hive_stores = stores if stores is not None else open_default_stores(manifest)
    forage_map = build_forage_map(manifest, clock)
    registry = build_provider_registry(manifest, environ, clock, forage_map, responders)
    fanner = build_fanner(manifest, forage_map, hive_stores.trail, clock)
    source = build_hive_stand_source(manifest, hive_stores.trail, clock)
    links = _build_links(manifest, source, clock)
    parts = HiveParts(
        manifest=manifest, registry=registry, fanner=fanner, stores=hive_stores, clock=clock
    )
    return _assemble_hive(parts, forage_map, source, links)


def _build_links(manifest: HiveManifest, source: HiveStandSource, clock: Clock) -> HiveLinks:
    """Probe the Hive Stand's one Cell and build the Queen<->Warden link around it.

    SAFETY: a fresh event loop for this one setup call, the seam where a sync composition-root
    function first reaches `HiveStandSource.cells` (an async `RealCellSource` method that here
    does no real I/O: it only reads this host's own already-probed capacity), mirroring
    `hivemind.cli.stores.open_trail`'s own `asyncio.run` seam (codingrules section 8.2).
    """
    cell = asyncio.run(source.cells())[0]
    return build_hive_links(manifest.hive.id, manifest.hive.node_id, cell, clock)


def _assemble_hive(
    parts: HiveParts, forage_map: ForageMap, source: HiveStandSource, links: HiveLinks
) -> Hive:
    """Build the Warden and Queen from `parts`, attach the link, and wrap it all as a Hive."""
    warden = Warden(links.warden_id, build_warden_deps(parts, source, links))
    queen = Queen(build_queen_deps(parts, forage_map))
    queen.attach_warden(links.queen_link)
    return Hive(
        manifest=parts.manifest,
        stores=parts.stores,
        registry=parts.registry,
        fanner=parts.fanner,
        source=source,
        warden=warden,
        queen=queen,
        warden_link=links.queen_link,
        clock=parts.clock,
    )


@asynccontextmanager
async def run_hive(hive: Hive) -> AsyncIterator[None]:
    """Lease the Hive Stand's Cell, run the Queen and Warden, and tear both down cleanly on exit.

    Args:
        hive: A Hive from `build_hive`, not yet started.

    Yields:
        Control to the caller, with `hive.queen` and `hive.warden` both ticking as background
        tasks; call `hive.queen.submit_goal`/`run_goal` inside the `async with` block.
    """
    await hive.warden.start()
    # Structured concurrency (codingrules section 11): both loops are owned by this one
    # asyncio.TaskGroup, which awaits them to completion when the block below exits, whether
    # cleanly or through an exception raised inside the caller's own `async with` body.
    async with asyncio.TaskGroup() as group:
        group.create_task(hive.queen.run())
        group.create_task(hive.warden.run())
        try:
            yield
        finally:
            # Stop the Queen first (codingrules section 8.8: she holds no session, nothing to
            # release), then the Warden, which releases its lease -- "left as found" -- before its
            # own run() loop is allowed to end.
            hive.queen.stop()
            await hive.warden.stop()
            # Closing the Queen's own end wakes the Warden's queen_link.receive() with a clean
            # sentinel (waggle.transport.memory.MemoryTransport.close's own contract), so nothing
            # is left awaiting a link neither side will ever write to again.
            await hive.warden_link.transport.close()


async def run_goal(
    hive: Hive,
    goal: str,
    *,
    clearance: HoneyClearance,
    timeout_s: float,
    on_event: Callable[[PheromoneEvent], None] | None = None,
) -> GoalReport:
    """Submit `goal` and poll until every one of its tasks is terminal, or `timeout_s` elapses.

    Must be called inside a `run_hive` block: it submits through `hive.queen`, which only ticks
    while `run_hive`'s own background tasks are running.

    Args:
        hive: A Hive whose Queen and Warden are running (inside an `async with run_hive(hive):`).
        goal: The goal text, as the human stated it.
        clearance: The goal's own data-sensitivity ceiling.
        timeout_s: The most wall time (on `hive.clock.monotonic()`) to poll before giving up.
        on_event: Called with every new trail event of interest, oldest first, as it lands; never
            called for a re-delivered event across polls.

    Returns:
        A GoalReport: `succeeded` is True only when every task reached SUCCEEDED before the
        timeout.
    """
    clock = hive.clock
    start = clock.monotonic()
    submitted_at = clock.now()
    goal_id = await hive.queen.submit_goal(goal, clearance=clearance)
    timed_out = await _poll_until_terminal(hive, goal_id, timeout_s, on_event)
    tasks = await hive.stores.chamber.list(TaskFilter(goal_id=goal_id))
    succeeded = (
        bool(tasks) and not timed_out and all(t.status is TaskStatus.SUCCEEDED for t in tasks)
    )
    return GoalReport(
        goal_id=goal_id,
        tasks=tasks,
        succeeded=succeeded,
        spend_usd=await _spend_since(hive, submitted_at),
        elapsed_s=clock.monotonic() - start,
        timed_out=timed_out,
    )


async def _poll_until_terminal(
    hive: Hive,
    goal_id: TaskId,
    timeout_s: float,
    on_event: Callable[[PheromoneEvent], None] | None,
) -> bool:
    """Poll chamber state and forward trail events until `goal_id`'s own tasks are all terminal.

    Returns:
        True once `timeout_s` elapsed first; False once every task reached a terminal status.
    """
    clock = hive.clock
    start = clock.monotonic()
    last_at: datetime | None = None
    last_ids: set[str] = set()
    while True:
        # hive inbox answer (a separate process against the same [hive] db) can only leave a
        # human's answer for the Queen to notice on its own next poll (hive.py's own module
        # docstring); this is that poll, run from here since hivemind.queen.queen is not this
        # dispatch's file to add the call to the Queen's own tick (hivemind.queen.questions.
        # sync_answers_from_chamber's own module docstring).
        await sync_answers_from_chamber(hive.queen)
        last_at, last_ids = await _forward_new_events(hive, on_event, last_at, last_ids)
        tasks = await hive.stores.chamber.list(TaskFilter(goal_id=goal_id))
        if tasks and all(is_terminal(task.status) for task in tasks):
            # The task's own terminal event (task.succeeded/failed) may have landed on the trail
            # after the query above already ran (the chamber and the trail are separate stores,
            # codingrules section 12): one more forward catches it before this stops polling, so
            # on_event's own stream always ends with the event that made the goal terminal.
            await _forward_new_events(hive, on_event, last_at, last_ids)
            return False
        if clock.monotonic() - start >= timeout_s:
            return True
        # External wait: the one real pause in this loop, always through the injected Clock so a
        # FakeClock-driven test controls every iteration instead of a real timer.
        await clock.sleep(_POLL_INTERVAL_S)


async def _forward_new_events(
    hive: Hive,
    on_event: Callable[[PheromoneEvent], None] | None,
    last_at: datetime | None,
    last_ids: set[str],
) -> tuple[datetime | None, set[str]]:
    """Query the trail since `last_at`, call `on_event` on every not-yet-seen one, advance the mark.

    Mirrors `hivemind.pheromone.trail.tail.follow`'s own "since is inclusive, track ids at the
    high-water mark too" technique, inlined here (rather than driving `follow` itself) because
    `run_goal`'s own loop already owns the polling cadence and needs to interleave a chamber
    check and `sync_answers_from_chamber` between reads, not just yield events forever.
    """
    page = await hive.stores.trail.query(TrailQuery(since=last_at))
    new_events = [
        event
        for event in page
        if not (last_at is not None and event.at == last_at and event.id in last_ids)
    ]
    if on_event is not None:
        for event in new_events:
            on_event(event)
    if not page:
        return last_at, last_ids
    newest_at = page[-1].at
    return newest_at, {event.id for event in page if event.at == newest_at}


async def _spend_since(hive: Hive, submitted_at: datetime) -> float:
    """Sum every `llm.call` occurrence's own cost recorded since `submitted_at`.

    See `GoalReport.spend_usd`'s own docstring for the Hive-wide, not goal-scoped, approximation
    this is. No per-goal Forage ledger exists this phase (`hivemind.forage.allocate.grant` is a
    pure, static allocator; codingrules section 8.10 names a live ledger a later-phase addition),
    so this is every model call the Fanner recorded on the trail since the goal was submitted --
    exact for the common case `run_goal` serves, one goal running at a time against one Hive.
    """
    events = await hive.stores.trail.query(
        TrailQuery(since=submitted_at, family="llm", kind="llm.call")
    )
    costs: list[float] = [
        event.usage.cost_usd
        for event in events
        if isinstance(event, LlmEvent)
        and event.usage is not None
        and event.usage.cost_usd is not None
    ]
    return sum(costs)
