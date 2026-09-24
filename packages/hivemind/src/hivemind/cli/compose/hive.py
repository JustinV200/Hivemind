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
    `hivemind.cli.compose.deps`, `.links`, `hivemind.cli.stores`, `hivemind.common.secrets` (the
    Hive's persisted signing key, for the Virtual side, and the untrusted-content scanner's key),
    `hivemind.guard.scanner`, `hivemind.pheromone`, `hivemind.queen`, `hivemind.wardens` and
    waggle only.

Key invariants:
    - `build_hive` never touches the network: every provider it constructs is lazy
      (`hivemind.llm.registry.ProviderRegistry.provider`'s own rule), and its own `asyncio.run`
      calls only probe this host's own capacity (`hivemind.cell.local.HiveStandSource.cells`, to
      seed the Queen<->Warden link's Cell) and, with a Virtual side, read or mint the Hive's
      signing key in the local secret store (`_hive_signer`).
    - `run_hive` always stops the Queen, stops the Warden (releasing its lease), awaits both of
      their `run()` tasks, closes the Queen<->Warden link and then every model provider's
      connections, in that order, whether its `async with` block exits cleanly or raises.
      Awaiting both tasks before its own `asyncio.TaskGroup` block ends is this dispatch's own
      shutdown-hygiene fix: without it, an exception propagating out of the caller's `async with
      run_hive(hive):` body would reach the TaskGroup while a tick might still be in flight, and
      the TaskGroup would cancel it itself rather than let the cooperative `stop()` above finish
      on its own.
    - `run_goal` never blocks past `timeout_s`: `GoalReport.timed_out` is True whenever the goal's
      own tasks are not all terminal by then, and `succeeded` is False in that case regardless of
      how far the goal got.
    - The Queen and the Hive Stand's Warden share one untrusted-content scanner (roadmap 10.6b),
      built from `[guard.untrusted_content]` and keyed from the secret store at `[hive]
      secrets_dir`; its key is minted on the first flag, never at build time.

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
from dataclasses import dataclass, replace
from datetime import datetime

from hivemind.brood_chamber import Task, TaskFilter, TaskStatus, is_terminal
from hivemind.cell import HoneyClearance
from hivemind.cell.local import HiveStandSource
from hivemind.cli.compose.deps import (
    HiveParts,
    HiveStores,
    build_enforcer,
    build_fanner,
    build_hive_stand_source,
    build_ledger,
    build_provider_registry,
    build_queen_deps,
    build_warden_deps,
    open_default_stores,
)
from hivemind.cli.compose.links import HiveLinks, build_hive_links
from hivemind.cli.compose.virtual_cells import VirtualCellsParts, build_virtual_cells
from hivemind.cli.stores import build_forage_map
from hivemind.common.secrets import FileSecretStore, load_or_mint_hive_signer
from hivemind.forage import ForageMap
from hivemind.guard.scanner import ContentHasher, ContentScanner, load_scan_patterns
from hivemind.llm import Fanner, ProviderRegistry, Responder
from hivemind.manifest import HiveManifest
from hivemind.pheromone import LlmEvent, PheromoneEvent, TrailQuery
from hivemind.queen import ForageLedger, Queen, WardenLink, sync_answers_from_chamber
from hivemind.wardens import Warden
from waggle.clock import Clock
from waggle.ids import TaskId
from waggle.signing import Ed25519Signer

__all__ = ["GoalReport", "Hive", "build_content_scanner", "build_hive", "run_goal", "run_hive"]

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
        queen: The Queen, not yet attached to `warden_link`: attaching is her `warden_spawn`
            enforcement point, an awaited Guard check, so `run_hive` does it (roadmap step 10.3).
        warden_link: The Queen's own end of the Queen<->Warden link; `run_hive` attaches it first
            and closes it on exit.
        clock: The injected time source every collaborator above shares.
        virtual_cells: `hivemind.cli.compose.virtual_cells.build_virtual_cells`'s own return
            value, when `[virtual_cells] backend` is set; `None` otherwise, in which case
            `run_hive` touches nothing Virtual-Cell-related at all (roadmap step 5.6).
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
    virtual_cells: VirtualCellsParts | None = None


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
    provider `registry` may later construct is lazy, and its `asyncio.run` calls (`_build_links`,
    `_hive_signer`) only probe this host's capacity and read the local secret store.

    Args:
        manifest: A HiveManifest loaded by `hivemind.manifest.load_manifest`.
        environ: The composition root's own environment mapping, read once for provider API keys.
        clock: Injected time source shared by every collaborator this builds.
        stores: A test's own in-memory `HiveStores`; `open_default_stores(manifest)` (real
            SQLite, `[hive] db`) when omitted.
        responders: Installed on every `kind = "fake"` provider this Hive constructs
            (`hivemind.cli.compose.deps.build_provider_registry`); `None` in production.

    Returns:
        A Hive not yet started (no lease, no tick, no Warden attached); pass it to `run_hive`.
    """
    hive_stores = stores if stores is not None else open_default_stores(manifest)
    forage_map = build_forage_map(manifest, clock)
    # Roadmap step 4.8: built before build_fanner, whose LedgerRecorder books each llm.call live.
    ledger = build_ledger(manifest, manifest.forage.reserve)
    registry = build_provider_registry(manifest, environ, clock, forage_map, responders)
    fanner = build_fanner(manifest, forage_map, hive_stores.trail, clock, ledger)
    source = build_hive_stand_source(manifest, hive_stores.trail, clock, hive_stores.leavings)
    links = _build_links(manifest, source, clock)
    # Roadmap step 5.6: None when `[virtual_cells] backend` is unset (virtual_cells' docstring).
    virtual_cells = build_virtual_cells(
        manifest, hive_stores.trail, clock, environ, hive_signer=_hive_signer(manifest)
    )
    parts = HiveParts(
        manifest=manifest,
        registry=registry,
        fanner=fanner,
        stores=hive_stores,
        clock=clock,
        enforcer=build_enforcer(manifest, hive_stores.trail, clock),  # Roadmap step 10.3.
    )
    extras = _AssemblyExtras(
        forage_map=forage_map,
        ledger=ledger,
        virtual_cells=virtual_cells,
        scanner=build_content_scanner(manifest),  # Roadmap step 10.6b.
    )
    return _assemble_hive(parts, source, links, extras)


def _build_links(manifest: HiveManifest, source: HiveStandSource, clock: Clock) -> HiveLinks:
    """Probe the Hive Stand's one Cell and build the Queen<->Warden link around it.

    SAFETY: a fresh event loop for this one setup call, the seam where a sync composition-root
    function first reaches `HiveStandSource.cells` (an async `RealCellSource` method that here
    does no real I/O: it only reads this host's own already-probed capacity), mirroring
    `hivemind.cli.stores.open_trail`'s own `asyncio.run` seam (codingrules section 8.2).
    """
    cell = asyncio.run(source.cells())[0]
    return build_hive_links(manifest.hive.id, manifest.hive.node_id, cell, clock)


def _hive_signer(manifest: HiveManifest) -> Ed25519Signer | None:
    """Load (or, on the Hive's first run, mint) its signing key when a Virtual side is set.

    Phase 5 open item 5: the Queen signs every Virtual Cell frame with this key, so it must be the
    same key after a restart; it lives in the secret store at the manifest's resolved `[hive]
    secrets_dir` (a test's manifest lives under its own `tmp_path`, so its key does too). `None`
    when `[virtual_cells] backend` is unset: `build_virtual_cells` then builds nothing, and a Hive
    with no Virtual side never writes a key it does not use.

    SAFETY: a fresh event loop for this one setup call, the same seam `_build_links` uses: the
    secret store is async, `build_hive` is a sync composition root, and the call is one small
    file read (or one write, the first time), never a network request.
    """
    if manifest.virtual_cells.backend is None:
        return None
    store = FileSecretStore(manifest.resolve_path(manifest.hive.secrets_dir))
    return asyncio.run(load_or_mint_hive_signer(store))


def build_content_scanner(manifest: HiveManifest) -> ContentScanner:
    """Build the Hive's untrusted-content scanner: the shipped patterns, `[guard]`'s thresholds.

    Roadmap step 10.6b: the Queen (chat messages) and the Hive Stand's Warden (its sub-bees' tool
    results) share this one scanner, so every flag on this node is hashed under one key. The key
    lives in the secret store at the manifest's resolved `[hive] secrets_dir`, beside the Hive's
    signing key, and is minted on the first flag, so building the scanner touches no disk.

    Args:
        manifest: A HiveManifest loaded by `hivemind.manifest.load_manifest`.

    Returns:
        A ContentScanner over `load_scan_patterns()` and `[guard.untrusted_content]`.

    Raises:
        hivemind.guard.GuardPolicyError: The shipped pattern file is unreadable or invalid.
    """
    store = FileSecretStore(manifest.resolve_path(manifest.hive.secrets_dir))
    return ContentScanner(
        load_scan_patterns(), manifest.guard.untrusted_content, ContentHasher(store)
    )


@dataclass(frozen=True, slots=True)
class _AssemblyExtras:
    """Builder outputs `_assemble_hive` needs beyond `parts`/`source`/`links` (codingrules 5.1)."""

    forage_map: ForageMap
    ledger: ForageLedger
    virtual_cells: VirtualCellsParts | None
    scanner: ContentScanner  # Roadmap step 10.6b: shared by the Queen and the Hive Stand's Warden.


def _assemble_hive(
    parts: HiveParts, source: HiveStandSource, links: HiveLinks, extras: _AssemblyExtras
) -> Hive:
    """Build the Warden and Queen from `parts` and wrap them as a Hive; `run_hive` attaches."""
    # The configured scanner replaces each deps bundle's shipped-default one (roadmap 10.6b).
    warden_deps = replace(build_warden_deps(parts, source, links), scanner=extras.scanner)
    warden = Warden(links.warden_id, warden_deps)
    queen_deps = build_queen_deps(parts, extras.forage_map, extras.ledger, extras.virtual_cells)
    queen = Queen(replace(queen_deps, scanner=extras.scanner))
    if extras.virtual_cells is not None:
        # Safe before run_hive/listener.start(): acquire() is only ever called from a tick, well
        # after both are running (hivemind.queen.cell_gate.provider's own module docstring).
        extras.virtual_cells.provider.bind_queen(queen)
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
        virtual_cells=extras.virtual_cells,
    )


@asynccontextmanager
async def run_hive(hive: Hive) -> AsyncIterator[None]:
    """Lease the Hive Stand's Cell, run the Queen and Warden, and tear both down cleanly on exit.

    Args:
        hive: A Hive from `build_hive`, not yet started.

    Yields:
        Control to the caller, with `hive.queen` and `hive.warden` both ticking as background
        tasks; call `hive.queen.submit_goal`/`run_goal` inside the `async with` block.

    Raises:
        hivemind.queen.WardenSpawnRefusedError: The Guard refused the Queen `warden:spawn`; nothing
            was started (roadmap step 10.3).
    """
    await _start(hive)
    # Structured concurrency (codingrules section 11): both loops are owned by this one
    # asyncio.TaskGroup, which awaits them to completion when the block below exits, whether
    # cleanly or through an exception raised inside the caller's own `async with` body.
    async with asyncio.TaskGroup() as group:
        queen_task = group.create_task(hive.queen.run())
        warden_task = group.create_task(hive.warden.run())
        try:
            yield
        finally:
            # Stop the Queen first (codingrules section 8.8: she holds no session, nothing to
            # release), then the Warden, which releases its lease -- "left as found" -- before its
            # own run() loop is allowed to end; both are cooperative signals (their own stop()),
            # never a cancel, and awaited to completion right here -- before this TaskGroup's own
            # __aexit__ runs -- so a caller's exception propagating through this block can never
            # have the TaskGroup itself cancel a tick still in flight (this dispatch's own rule 4).
            await hive.queen.stop()
            await hive.warden.stop()
            await asyncio.gather(queen_task, warden_task)
            if hive.virtual_cells is not None:
                # Every Virtual Cell, dormant ones included: hivemind.queen.cell_gate.shutdown.
                await hive.virtual_cells.retire_all()
                # Stop accepting and close every Virtual Cell connection last: nothing above this
                # still reads from a WardenLink once the Queen and Warden are both fully stopped.
                await hive.virtual_cells.listener.stop()
            # Closing the Queen's own end wakes the Warden's queen_link.receive() with a clean
            # sentinel (waggle.transport.memory.MemoryTransport.close's own contract), so nothing
            # is left awaiting a link neither side will ever write to again.
            await hive.warden_link.transport.close()
            # Last, once nothing can call a model any more: release every provider's pooled
            # connections (hivemind.llm.ProviderRegistry.aclose, bounded per provider), which a
            # long-running `hive serve` would otherwise leak for good.
            await hive.registry.aclose()


async def _start(hive: Hive) -> None:
    """Attach the Hive Stand's Warden, open the Virtual side, then lease the Hive Stand's Cell.

    Split out of `run_hive` for codingrules 5.1's function length only; everything here happens
    before the Queen's and the Warden's own loops start.
    """
    # Roadmap step 10.3: admitting the Hive Stand's Warden is the Queen's warden_spawn point, an
    # awaited Guard check (and a `warden.spawned` row), so it happens here, before anything runs.
    await hive.queen.attach_warden(hive.warden_link)
    if hive.virtual_cells is not None:
        # Roadmap step 5.6: start accepting Virtual Cells' own control connections, THEN reconcile
        # the live table from every registered backend's own list_cells (hivemind.hive.lifecycle.
        # CellLifecycle.reconcile's own contract: called once, before any other method). The
        # listener goes first because reconcile constructs every backend, and a Docker or QEMU
        # backend's QueenEndpoint carries the listener's bound port, which only exists after
        # start() (the first real Docker run failed on exactly this). Both happen before
        # hive.warden.start()/the TaskGroup in run_hive, so a Cell dialling back in while the Queen
        # is still coming up is never dropped for connecting "too early".
        await hive.virtual_cells.listener.start(hive.queen)
        await hive.virtual_cells.lifecycle.reconcile(hive.manifest.hive.id)
    await hive.warden.start()


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
    # The deadline runs from `start`, before planning: submit_goal's own model call can take
    # minutes on a local model, and "never blocks past timeout_s" (module docstring) has to
    # include it.
    submission = _Submission(start=start, at=submitted_at)
    timed_out = await _poll_until_terminal(hive, goal_id, timeout_s, on_event, submission)
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


@dataclass(frozen=True, slots=True)
class _Submission:
    """When a goal was submitted, on both clocks `_poll_until_terminal` needs (codingrules 5.1)."""

    start: float  # `clock.monotonic()` at submission: what `timeout_s` counts from.
    at: datetime  # `clock.now()` at submission: where the forwarded trail view begins.


async def _poll_until_terminal(
    hive: Hive,
    goal_id: TaskId,
    timeout_s: float,
    on_event: Callable[[PheromoneEvent], None] | None,
    submission: _Submission,
) -> bool:
    """Poll chamber state and forward trail events until `goal_id`'s own tasks are all terminal.

    Args:
        hive: The running Hive whose chamber and trail are polled.
        goal_id: The goal whose tasks decide when polling stops.
        timeout_s: The most wall time to poll, measured from `start`.
        on_event: Called with every new trail event of interest; None to forward nothing.
        submission: When the goal was submitted. `timeout_s` counts from `submission.start`, so
            planning time (`submit_goal`'s own model call) is inside the budget; forwarded
            events begin at `submission.at`, so a store that already holds earlier runs never
            replays their whole history into this run's view.

    Returns:
        True once `timeout_s` elapsed first; False once every task reached a terminal status.
    """
    clock = hive.clock
    start = submission.start
    last_at: datetime | None = submission.at
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
