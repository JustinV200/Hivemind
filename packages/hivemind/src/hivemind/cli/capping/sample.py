"""Provide `hive capping audit sample|rates`: sample terminal proposals for judge review.

Roadmap step 4.11's own deliverable: `hive capping audit sample [--tier T] [--rate R]
--fake-judge` samples every terminal (VERIFIED or ROLLED_BACK) proposal on the trail at its tier's
own `audit_rate` (or `--rate`, when given) and reviews it through `hivemind.supervision.capping.
audit_completed`, printing per-tier `AuditRates` and any findings; `hive capping audit rates`
prints the same rates, reconstructed from every past `capping.audited` event, with no fresh
sampling. Named `sample`, not a bare `--sample` flag on `hive capping audit` itself (the roadmap's
own wording): codingrules section 5.1's parameter cap has no room on one function for both a
`--sample` flag and this command's other four flags (`--tier`, `--rate`, `--fake-judge`,
`--manifest`, `--db`) once `ctx` is counted too -- this dispatch's own report flags the deviation.
The trail never carries a proposal's own action content (codingrules section 12: ids, counts and
enums only), so `_reconstruct_proposal` below rebuilds only what the trail actually kept (the task
id, the Cell id and the tier from `capping.proposed`, via `hivemind.cli.capping.queue.
track_proposals`) and fills the rest with clearly-labelled placeholders -- the judge review this
command runs is therefore a review of that placeholder, never of the bee's real diff or command, a
limitation this module states plainly. The model-backed reviewer (`hivemind.wardens.judge.
ModelJudgeReviewer` on `ModelSlot.JUDGE`) is what a running Warden's gate uses; spending real
model calls on placeholders would prove nothing, so this detached command requires `--fake-judge`
and uses `hivemind.supervision.capping.FakeJudgeReviewer`, scripted with one APPROVE verdict per
sampled proposal, until the trail (or the Basket, roadmap 9.2a) carries proposal content.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Mounted by `hivemind.cli.capping.app` as `audit`.
    Calls into `hivemind.pheromone`, `hivemind.supervision.capping` (AuditDeps, AuditRates,
    audit_completed, ...), `hivemind.cell` (CellIdentity), `hivemind.forage.tempo` (Tempo),
    `hivemind.cli.capping.queue` and `hivemind.cli.stores` only.

Key invariants:
    - `sample` never writes anywhere but through `audit_completed` itself (the `capping.audited`
      event and any Alarm it raises); this module writes nothing of its own.

See Also:
    - .claude/roadmap.md step 4.11 for this module's own deliverable, verbatim.
    - hivemind.supervision.capping.audit.sampler for audit_completed, this module's one core call.
    - hivemind.cli.capping.queue for track_proposals, this module's one reuse.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer

from hivemind.cell import CellIdentity, HoneyClearance
from hivemind.cli.capping.queue import KIND_TO_STATE, ProposalTrack, capping_events, track_proposals
from hivemind.cli.stores import (
    DEFAULT_MANIFEST,
    DbOption,
    ManifestOption,
    load_manifest_or_exit,
    open_trail,
    resolve_db,
)
from hivemind.forage.tempo import Tempo
from hivemind.manifest import HiveManifest
from hivemind.pheromone import MAX_QUERY_LIMIT, PheromoneTrail, TrailQuery
from hivemind.supervision.capping import (
    AuditDeps,
    AuditRates,
    AuditSampler,
    FakeJudgeReviewer,
    InMemoryFindingsSink,
    JudgeOutcome,
    JudgeRubric,
    JudgeVerdict,
    Proposal,
    ProposalState,
    RiskTier,
    TierTable,
    load_judge_rubrics,
    load_tiers,
)
from hivemind.supervision.capping.audit.sampler import audit_completed
from waggle.clock import Clock, SystemClock
from waggle.ids import CellId, MessageId, TaskId, new_worker_id
from waggle.messages.capping import ActionKind, ProposedAction

app = typer.Typer(name="audit", help="Sample terminal proposals for judge review.")

__all__ = ["app"]

_TERMINAL_AUDIT_STATES = frozenset({ProposalState.VERIFIED, ProposalState.ROLLED_BACK})
# A placeholder ProposedAction for a proposal reconstructed from the trail: the trail never carries
# a proposal's own diff or command (module docstring), so this labels exactly that gap rather than
# inventing content the judge could mistake for the bee's real work.
_UNAVAILABLE_ACTION = ProposedAction(
    kind=ActionKind.COMMAND,
    summary="Reconstructed from the trail for `hive capping audit`; the real action is not on "
    "the trail (codingrules section 12).",
    diff=None,
    diff_sha256=None,
    command=("(unavailable: not carried on the trail)",),
    cwd=None,
    paths=(),
    steps=(),
)


def _parse_tier(value: str) -> RiskTier:
    """Validate `--tier` through RiskTier's own check, matching typer's usual bad-input shape."""
    try:
        return RiskTier(value.upper())
    except ValueError as exc:
        raise typer.BadParameter(f"{value!r} is not a RiskTier.") from exc


@dataclass(frozen=True, slots=True)
class _AuditCliContext:
    """The resolved manifest and db path, shared by both `hive capping audit` subcommands."""

    manifest: HiveManifest
    db: Path


@app.callback()
def audit_group_callback(
    ctx: typer.Context, manifest: ManifestOption = DEFAULT_MANIFEST, db: DbOption = None
) -> None:
    """Load the manifest and resolve the db file `sample`/`rates` below both share.

    `--manifest`/`--db` live here, not on `sample` itself, because codingrules section 5.1's
    five-parameter cap leaves no room on `sample` for both its own four flags and these two --
    the same reason `hivemind.cli.forage` and `hivemind.cli.memory.wax` share them this way.
    """
    loaded = load_manifest_or_exit(manifest)
    ctx.obj = _AuditCliContext(manifest=loaded, db=resolve_db(manifest, db))


@app.command("sample")
def audit_sample_command(
    ctx: typer.Context,
    tier: Annotated[
        str | None, typer.Option("--tier", help="Only this RiskTier's proposals.")
    ] = None,
    rate: Annotated[
        float | None, typer.Option("--rate", help="Override every sampled tier's own audit_rate.")
    ] = None,
    fake_judge: Annotated[
        bool,
        typer.Option(
            "--fake-judge",
            help="Use FakeJudgeReviewer; required, since the trail carries no proposal content.",
        ),
    ] = False,
) -> None:
    """Sample terminal (VERIFIED/ROLLED_BACK) proposals from the trail and review them."""
    if not fake_judge:
        # SAFETY: top of a CLI command (codingrules section 10): refuse cleanly rather than
        # silently picking a reviewer the operator did not ask for.
        typer.echo(
            "The trail carries no proposal content for a model judge to review (the Warden's own "
            "gate runs ModelJudgeReviewer); pass --fake-judge to audit with FakeJudgeReviewer.",
            err=True,
        )
        raise typer.Exit(code=2)
    cli_ctx: _AuditCliContext = ctx.obj
    trail = open_trail(cli_ctx.db)
    tier_filter = _parse_tier(tier) if tier is not None else None
    rates = asyncio.run(_run_audit(cli_ctx.manifest, trail, tier_filter, rate))
    _print_rates(rates)


@app.command("rates")
def audit_rates_command(ctx: typer.Context) -> None:
    """Print every tier's AuditRates, rebuilt from past `capping.audited` events; no sampling."""
    cli_ctx: _AuditCliContext = ctx.obj
    trail = open_trail(cli_ctx.db)
    rates = asyncio.run(_rates_from_trail(trail))
    _print_rates(rates)


@dataclass(frozen=True, slots=True)
class _AuditRun:
    """Every collaborator one `hive capping audit sample` run shares, bundled per codingrules 5.1.

    `rates` is mutated in place across every sampled proposal (`AuditRates.record_sample`'s own
    documented shape); everything else is read-only for the whole run.
    """

    tier_filter: RiskTier | None
    rate_override: float | None
    tier_table: TierTable
    rubrics: dict[RiskTier, JudgeRubric]
    sampler: AuditSampler
    sink: InMemoryFindingsSink
    trail: PheromoneTrail
    identity: CellIdentity
    clock: Clock
    rates: AuditRates


async def _run_audit(
    manifest: HiveManifest,
    trail: PheromoneTrail,
    tier_filter: RiskTier | None,
    rate_override: float | None,
) -> AuditRates:
    """Sample every eligible terminal proposal once, and return the tallied AuditRates."""
    tiers_path = (
        manifest.resolve_path(manifest.supervision.capping_tiers_file)
        if manifest.supervision.capping_tiers_file is not None
        else None
    )
    run = _AuditRun(
        tier_filter=tier_filter,
        rate_override=rate_override,
        tier_table=load_tiers(tiers_path),
        rubrics=load_judge_rubrics(),
        sampler=AuditSampler(),
        sink=InMemoryFindingsSink(),
        trail=trail,
        identity=CellIdentity(
            hive_id=manifest.hive.id, node_id=manifest.hive.node_id, actor="human"
        ),
        clock=SystemClock(),
        rates=AuditRates(),
    )
    tracks = track_proposals(await capping_events(trail))
    for proposal_id, track in tracks.items():
        await _maybe_audit_one(proposal_id, track, run)
    return run.rates


async def _maybe_audit_one(proposal_id: str, track: ProposalTrack, run: _AuditRun) -> None:
    """Audit one tracked proposal if it is terminal, tier-matched, and its tier is configured."""
    state = KIND_TO_STATE.get(track.last_kind)
    if state not in _TERMINAL_AUDIT_STATES or not track.tier:
        return  # Not yet terminal, or this trail never recorded a tier for it: nothing to sample.
    tier_value = RiskTier(track.tier)
    if run.tier_filter is not None and tier_value is not run.tier_filter:
        return
    spec = run.tier_table.tiers.get(tier_value)
    if spec is None:
        return  # No configured ladder for this tier at all; audit has nothing to score against.
    effective_rate = run.rate_override if run.rate_override is not None else spec.audit_rate
    effective_spec = spec.model_copy(update={"audit_rate": effective_rate})
    reviewer = _scripted_reviewer(run.rubrics.get(tier_value))
    deps = AuditDeps(
        sampler=run.sampler,
        reviewer=reviewer,
        rubrics=run.rubrics,
        sink=run.sink,
        trail=run.trail,
        identity=run.identity,
        clock=run.clock,
    )
    proposal = _reconstruct_proposal(MessageId(proposal_id), track, tier_value)
    await audit_completed(deps, proposal, effective_spec, run.rates)


def _scripted_reviewer(rubric: JudgeRubric | None) -> FakeJudgeReviewer:
    """Build a FakeJudgeReviewer scripted with one APPROVE, or none if this tier has no rubric.

    Scripted APPROVE (module docstring): this reviews a reconstructed placeholder, so the point is
    exercising the audit path, not a real verdict on the bee's own work; `audit_completed` itself
    raises `CappingError` when `rubric` is None, which this leaves for it to raise.
    """
    reviewer = FakeJudgeReviewer()
    if rubric is not None:
        reviewer.script(
            JudgeVerdict(outcome=JudgeOutcome.APPROVE, reasons=(), rubric_id=rubric.rubric_id)
        )
    return reviewer


def _reconstruct_proposal(proposal_id: MessageId, track: ProposalTrack, tier: RiskTier) -> Proposal:
    """Build the minimal Proposal `audit_completed` needs, from what the trail actually kept.

    Every field the trail cannot recover is filled with a clearly-labelled placeholder
    (`_UNAVAILABLE_ACTION`, an empty `postconditions` tuple, a freshly minted placeholder
    `proposer`); module docstring.
    """
    return Proposal(
        id=proposal_id,
        task_id=TaskId(track.task_id),
        cell_id=CellId(track.cell_id),
        proposer=new_worker_id(SystemClock()),
        risk_tier=tier,
        action=_UNAVAILABLE_ACTION,
        postconditions=(),
        tempo=Tempo(),
        clearance=HoneyClearance.C1,
        reason="Reconstructed from the trail for `hive capping audit`.",
    )


async def _rates_from_trail(trail: PheromoneTrail) -> AuditRates:
    """Rebuild AuditRates from every past `capping.audited` event; no fresh sampling."""
    events = await trail.query(TrailQuery(kind="capping.audited", limit=MAX_QUERY_LIMIT))
    rates = AuditRates()
    for event in events:
        tier = RiskTier(str(event.payload.get("tier", "")))
        outcome = str(event.payload.get("outcome", ""))
        rates.record_sample(tier, failed=outcome == JudgeOutcome.REJECT.value)
    return rates


def _print_rates(rates: AuditRates) -> None:
    """Print one line per RiskTier with at least one sample: sampled, failed, failure_rate."""
    typer.echo(f"{'TIER':<24}  {'SAMPLED':>7}  {'FAILED':>6}  FAILURE_RATE")
    for tier in RiskTier:
        sampled = rates.sampled(tier)
        if sampled == 0:
            continue
        failure_rate = rates.failure_rate(tier)
        typer.echo(f"{tier.value:<24}  {sampled:>7}  {rates.failed(tier):>6}  {failure_rate:.2f}")
