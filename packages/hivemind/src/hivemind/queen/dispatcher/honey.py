"""Consult the Honey Store for the Queen, before an assignment and before a plan (roadmap 7.9).

Honey is the Hive's ripened, searchable knowledge (the cold tier of memory); consulting it before
acting is how knowledge compounds across runs. Roadmap step 7.9 names two consultations, both the
Queen's pre-check: the planner queries Honey for the goal's targets (`consult_for_plan`, whose
hits `hivemind.queen.planner.plan_goal` renders into the plan prompt), and the dispatcher queries
it for the task and the chosen Cell's known quirks (`consult_for_assignment`, whose hits ride on
`TaskAssign.honey`). The assignment pre-check reads as the Worker-to-be would: its default
`honey:read` scopes, the task's clearance, the Cell's tier. It runs two searches -- the objective
over every readable scope, and the objective within the Cell's own `cell:<id>` history (where
cleared and expired Cell Wax ripens, roadmap 7.9a) -- merges them by reference keeping each hit's
best score, and puts the chosen Cell's live WRITTEN Cell Wax (a Queen-written caution about one
Cell, never a Honey row) first as hits of its own. Both consultations record
`queen.honey_consulted` (counts only). Neither ever stops the work it precedes: a failure is
logged and yields no hits, and each is bounded by a timeout derived from the embed timeout, since
a search may wait on the EMBEDDER slot but never on ripening.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `hivemind.queen.
    dispatcher`. `consult_for_assignment` is called by `hivemind.queen.dispatcher.ready.
    _send_grant_and_assign` for fresh dispatches, retries and resumes alike; `consult_for_plan`
    and `record_consulted` by `hivemind.queen.goal_submission.submit_goal`. Calls into
    `hivemind.cell`, `hivemind.honey_store` (the retriever, scopes, the reader ceiling, the hit
    budget), `hivemind.memory` (live Cell Wax), `hivemind.queen.deps`, `hivemind.queen.trail`,
    `hivemind.workers.roles.house_bee` (`bee_or_none`) and waggle.

Key invariants:
    - A hit is never labelled above the reader's ceiling: the retriever filters before ranking,
      and live wax is read at that same ceiling, so `TaskAssign`'s own clearance check holds.
    - Neither consultation raises: every failure is logged (codes and ids) and yields nothing.
    - At most `[honey.retrieval] precheck_max_hits` hits (0 turns both consultations off), packed
      into `budget_fraction` of the reading slot's window, capped at `max_budget_tokens`.
    - A Night Veil Cell's pre-check records no trail event and no log line at all, exactly like
      the Night Veil reader's own query and that Cell's own deposits (ADR-0035).

See Also:
    - docs/adr/0035-honey-store-sqlite-fts5-sqlite-vec.md for scopes, ceilings and the budget.
    - hivemind.honey_store.honey for HoneyRetriever, the one search both consultations run.
    - hivemind.queen.dispatcher.ready for the assignment this pre-check feeds.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from hivemind.brood_chamber import Task
from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.common.errors import HiveMindError
from hivemind.common.logging import get_logger
from hivemind.forage.slots import ModelSlot
from hivemind.honey_store import (
    HoneyAccess,
    HoneyReader,
    HoneySearch,
    SearchOutcome,
    cell_scope,
    folder_for_scope,
    queen_read_capabilities,
    reader_ceiling,
    worker_read_capabilities,
)
from hivemind.honey_store.honey import pack_hits
from hivemind.llm import UnresolvableSlotError
from hivemind.memory import CellWax, cap_wax_for_hot_state
from hivemind.memory.cell_wax import WaxState
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.trail import record_event
from hivemind.workers.roles.house_bee import bee_or_none
from waggle.ids import TaskId, new_worker_id
from waggle.messages.honey import HoneyHit, HoneyProvenance
from waggle.messages.honey.hit import MAX_EXCERPT_CHARS

CONSULTED_KIND = "queen.honey_consulted"  # The one trail event every consultation records.
ASSIGN_STAGE = "assign"  # The stage a pre-check before a TaskAssign records.
PLAN_STAGE = "plan"  # The stage the planner's consultation records.
WAX_HIT_SCORE = (
    1.0  # Live wax is the Queen's own caution about the very Cell: it always ranks first.
)
WAX_FOLDER = "wax"  # A Cell's live wax is browsed at /cells/<cell id>/wax/<wax id> (roadmap 7.10).
PRECHECK_SEARCHES = 2  # The objective everywhere, then the objective in the Cell's own history.
# Beyond the searches' own embed timeouts: the local SQLite reads around them, generously.
SEARCH_SLACK_S = 5.0
# What a failed consultation can raise: a Honey Store or memory refusal, a malformed value, the
# consultation's own timeout, or the database itself. Anything else is a bug and propagates.
_CONSULT_FAILURES = (HiveMindError, ValueError, TimeoutError, sqlite3.Error)

__all__ = [
    "ASSIGN_STAGE",
    "CONSULTED_KIND",
    "PLAN_STAGE",
    "PRECHECK_SEARCHES",
    "SEARCH_SLACK_S",
    "WAX_FOLDER",
    "WAX_HIT_SCORE",
    "Consultation",
    "consult_for_assignment",
    "consult_for_plan",
    "record_consulted",
]

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Consultation:
    """What one consultation found, and the counts `queen.honey_consulted` records about it.

    Attributes:
        hits: The hits to attach, in order: live wax first, then Honey by score.
        wax: How many of `hits` are live Cell Wax rather than Honey rows.
        tokens: What `hits` cost, by the Honey Store's own hit estimate.
        withheld: Top matches the reader's scope or clearance kept back, by the first (the
            broadest) search: a search narrowed to one scope also counts what it narrowed away.
        vector_used: Whether vector search took part in either search.
    """

    hits: tuple[HoneyHit, ...]
    wax: int
    tokens: int
    withheld: int
    vector_used: bool


async def consult_for_assignment(
    deps: QueenDeps, task: Task, link: WardenLink
) -> tuple[HoneyHit, ...]:
    """Find what the Hive knows about `task` and its chosen Cell, for `TaskAssign.honey`.

    Args:
        deps: The Queen's collaborators; `honey` (None skips everything), `memory` (live wax),
            `bound_for` (the WORKER window the budget scales by), `identity` and `trail`.
        task: The task about to be assigned; its objective is the query, its clearance the
            principal's ceiling.
        link: The chosen Cell's Warden link; the Cell's id scopes the second search and its tier
            caps what the Worker-to-be may read.

    Returns:
        At most `precheck_max_hits` hits, live wax first; empty when no Honey Store is wired,
        the pre-check is off, or the consultation failed (logged, never raised).
    """
    access = deps.honey
    if access is None or access.retrieval.precheck_max_hits == 0:
        return ()  # No Honey Store wired, or the operator turned the pre-check off.
    is_night_veil = link.cell.comb_shield is CombShieldLevel.NIGHT_VEIL
    try:
        # Bounded whole: two searches (each embedding the query at most once) plus local SQLite.
        async with asyncio.timeout(_consult_timeout_s(access)):
            consultation = await _precheck(deps, access, task, link)
            if not is_night_veil:
                # A Night Veil pre-check leaves no trail at all, like that reader's own query.
                await record_consulted(deps, task.id, ASSIGN_STAGE, consultation)
    except _CONSULT_FAILURES as error:
        # Nothing about a Night Veil Cell's work outlives its teardown, a failed pre-check
        # included: no log line either, exactly as intake treats that Cell's deposits.
        if not is_night_veil:
            log.warning("queen.precheck_failed", task_id=task.id, reason=_reason(error))
        return ()
    return consultation.hits


async def consult_for_plan(
    deps: QueenDeps, goal: str, clearance: HoneyClearance
) -> Consultation | None:
    """Find what the Hive knows about a goal before planning it, as the Queen reads.

    Args:
        deps: The Queen's collaborators; `honey` (None skips everything), `bound_for` (the QUEEN
            window the budget scales by) and `identity`.
        goal: The goal text: the query.
        clearance: The goal's own ceiling: the Queen reads every scope, but no hit above this.

    Returns:
        The consultation (its hits for `PlanBrief.honey`); None when no Honey Store is wired,
        the pre-check is off, or the consultation failed (logged, never raised). The caller
        records it once the goal has an id (`record_consulted`).
    """
    access = deps.honey
    if access is None or access.retrieval.precheck_max_hits == 0:
        return None  # No Honey Store wired, or the operator turned the pre-check off.
    reader = HoneyReader(
        requester=deps.identity.hive_id,
        capabilities=queen_read_capabilities(),
        ceiling=clearance,
        is_night_veil=False,
    )
    try:
        # One search (at most one embedding of the goal) plus local SQLite, bounded whole.
        async with asyncio.timeout(_consult_timeout_s(access)):
            budget = _budget_tokens(access, deps, ModelSlot.QUEEN)
            search = HoneySearch(goal, reader, (), access.retrieval.precheck_max_hits, budget)
            outcome = await access.retriever.search_outcome(search)
    except _CONSULT_FAILURES as error:
        log.warning("queen.plan_consult_failed", reason=_reason(error))
        return None
    return _consultation((), (outcome,), budget, access.retrieval.precheck_max_hits)


async def record_consulted(
    deps: QueenDeps, subject_id: TaskId, stage: str, consultation: Consultation
) -> None:
    """Record `queen.honey_consulted` for one consultation: counts and flags, never hit text.

    Args:
        deps: The Queen's collaborators; `trail`, `clock` and `identity` are what this writes with.
        subject_id: The task (an assignment) or the goal (a plan) the consultation was for.
        stage: `ASSIGN_STAGE` or `PLAN_STAGE`.
        consultation: What the consultation found.
    """
    await record_event(
        deps,
        CONSULTED_KIND,
        subject_id,
        stage=stage,
        hits=len(consultation.hits),
        wax=consultation.wax,
        tokens=consultation.tokens,
        withheld=consultation.withheld,
        vector_used=consultation.vector_used,
    )


async def _precheck(
    deps: QueenDeps, access: HoneyAccess, task: Task, link: WardenLink
) -> Consultation:
    """Run the pre-check's two searches and its wax read, and assemble what they found."""
    reader = _worker_reader(deps, access, task, link)
    budget = _budget_tokens(access, deps, ModelSlot.WORKER)
    limit = access.retrieval.precheck_max_hits
    objective = task.spec.objective
    # External awaits: each search may embed the objective once (bounded by embed_timeout_s,
    # after which it searches full text only); the rest is local SQLite.
    everywhere = await access.retriever.search_outcome(
        HoneySearch(objective, reader, (), limit, budget)
    )
    on_cell = await access.retriever.search_outcome(
        HoneySearch(objective, reader, (cell_scope(link.cell.id),), limit, budget)
    )
    wax = await _live_wax_hits(deps, link, reader.ceiling, limit)
    return _consultation(wax, (everywhere, on_cell), budget, limit)


def _worker_reader(
    deps: QueenDeps, access: HoneyAccess, task: Task, link: WardenLink
) -> HoneyReader:
    """Read as the Worker-to-be: its default scopes, the task's clearance, the Cell's tier."""
    # The Worker does not exist yet (its Warden spawns it), so its own bee scope is a fresh,
    # never-used id: the read set is exactly a Worker's, minus a bee folder nobody wrote to yet.
    capabilities = worker_read_capabilities(
        task.id, task.goal_id, link.cell.id, new_worker_id(deps.clock)
    )
    clearance = task.spec.clearance
    ceiling = reader_ceiling(clearance, clearance, link.cell.comb_shield, access.clearance.matrix)
    return HoneyReader(
        requester=deps.identity.hive_id,
        capabilities=capabilities,
        ceiling=ceiling,
        is_night_veil=link.cell.comb_shield is CombShieldLevel.NIGHT_VEIL,
    )


def _budget_tokens(access: HoneyAccess, deps: QueenDeps, slot: ModelSlot) -> int:
    """Scale the result budget by the reading slot's window, capped at `max_budget_tokens`.

    Raises:
        ValueError: `slot` has no binding to read a window from (the consultation is skipped).
    """
    try:
        window = deps.bound_for(slot).context_window
    except (UnresolvableSlotError, KeyError) as error:
        # The same two shapes housekeeping's own RIPENER lookup absorbs: a real registry's
        # unresolvable slot, or a test's plain-dict lookup. No window, no budget to scale.
        raise ValueError(f"No {slot.manifest_key} binding to size a Honey budget by.") from error
    retrieval = access.retrieval
    return min(int(window * retrieval.budget_fraction), retrieval.max_budget_tokens)


async def _live_wax_hits(
    deps: QueenDeps, link: WardenLink, ceiling: HoneyClearance, limit: int
) -> tuple[HoneyHit, ...]:
    """Render the chosen Cell's live WRITTEN wax at or below `ceiling` as hits, worst first."""
    cell = link.cell
    # Local SQLite (or memory), milliseconds; the store filters by the ceiling itself.
    written = await deps.memory.list_wax(cell.id, frozenset({WaxState.WRITTEN}), ceiling)
    now = deps.clock.now()
    # A note past its own expiry is no longer live, even before the next sweep marks it EXPIRED.
    live = [wax for wax in written if wax.expires_at is None or wax.expires_at > now]
    return tuple(_wax_hit(wax, link) for wax in cap_wax_for_hot_state(live, limit))


def _wax_hit(wax: CellWax, link: WardenLink) -> HoneyHit:
    """Render one live wax note as a hit: its browser path, severity, text and provenance."""
    scope = cell_scope(wax.cell_id)
    excerpt = f"{wax.text}\nReason: {wax.reason}" if wax.reason else wax.text
    return HoneyHit(
        honey_ref=f"{folder_for_scope(scope)}/{WAX_FOLDER}/{wax.id}",
        title=f"Cell Wax {wax.severity.value}",
        excerpt=excerpt[:MAX_EXCERPT_CHARS],
        score=WAX_HIT_SCORE,
        scope=scope,
        clearance=wax.clearance.to_wire(),
        origin_tier=link.cell.comb_shield.to_wire(),
        provenance=HoneyProvenance(
            task_id=wax.task_id,
            cell_id=wax.cell_id,
            bee=bee_or_none(wax.proposer),
            observed_at=wax.proposed_at,
        ),
    )


def _consultation(
    wax: tuple[HoneyHit, ...], outcomes: Sequence[SearchOutcome], budget: int, limit: int
) -> Consultation:
    """Merge the searches' hits after the wax, cap them, and pack them into the budget."""
    ranked = _merged_by_ref(hit for outcome in outcomes for hit in outcome.response.hits)
    # Wax first (the Queen's own caution about this very Cell), then Honey by score; the cap
    # counts both, and the budget packs in that order, so a caution is never crowded out.
    packed = pack_hits((*wax, *ranked)[:limit], budget)
    wax_refs = {hit.honey_ref for hit in wax}
    return Consultation(
        hits=packed.hits,
        wax=sum(1 for hit in packed.hits if hit.honey_ref in wax_refs),
        tokens=packed.token_count,
        # The broad search alone: the Cell-only one counts every other scope's match as withheld.
        withheld=outcomes[0].response.filtered_count if outcomes else 0,
        vector_used=any(outcome.vector_used for outcome in outcomes),
    )


def _merged_by_ref(hits: Iterable[HoneyHit]) -> tuple[HoneyHit, ...]:
    """Keep each reference once, at its best score, best first (ties broken by reference)."""
    best: dict[str, HoneyHit] = {}
    # A row found by both searches is the same row: keep whichever copy scored higher.
    for hit in hits:
        kept = best.get(hit.honey_ref)
        if kept is None or hit.score > kept.score:
            best[hit.honey_ref] = hit
    return tuple(sorted(best.values(), key=lambda hit: (-hit.score, hit.honey_ref)))


def _consult_timeout_s(access: HoneyAccess) -> float:
    """Bound one consultation: every search's embed timeout, plus slack for local SQLite."""
    return PRECHECK_SEARCHES * access.retrieval.embed_timeout_s + SEARCH_SLACK_S


def _reason(error: Exception) -> str:
    """Name a failure by its stable code when it has one, else by its class, for a log line."""
    return error.code if isinstance(error, HiveMindError) else type(error).__name__
