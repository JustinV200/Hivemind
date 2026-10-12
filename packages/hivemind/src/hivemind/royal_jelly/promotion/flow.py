"""Define PromotionFlow: request, scaffold, quarantine, then promote or reject, with one retry.

Roadmap 9.6. The flow is the Royal Jelly Lab's spine: it takes a `ToolRequestBrief` and walks the
tool through the state machine of `hivemind.royal_jelly.tool_state` (REQUESTED, SCAFFOLDED,
QUARANTINING, then PROMOTED or REJECTED), writing one `tool.*` event per stage. It scaffolds
(`Scaffolder`), asks a sandbox runner to prove the package (`SandboxRunner`), and on a passing
report promotes through the Comb Registry, deposits Nectar describing the tool and announces it.
A failing report buys exactly one more scaffold, this time told what failed (the state edge
QUARANTINING to SCAFFOLDED); a second failure, a scaffold that cannot be produced, a sandbox that
cannot be reached and any refusal by the registry (no force flag exists) all end as `tool.rejected`
with the failing gate or the refusal's stable code.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.promotion. Run
    once per request by the Queen's tool job (HIVE scope, `QueenPrincipal`) or by a Warden for its
    own Cell (CELL scope, `WardenPrincipal`); calls only its ports, the registry protocol, the
    trail and the tool state machine. It holds no state between runs.

Key invariants:
    - Every state change goes through `transition`, so no edge happens that Appendix C does not
      list; the retry is the one backwards edge.
    - The registry is the only way to a PROMOTED row, and its refusal (`PromotionRefusedError`) is
      a rejection, never retried and never worked around.
    - Honey and the announcer run after the promotion is durable; a failure in either is logged
      and never undoes it.
    - `max_retries` bounds the scaffolds at `1 + max_retries`; the default is the roadmap's one.

See Also:
    - docs/adr/0046 and docs/adr/0047 for the promotion and quarantine rules.
    - hivemind.royal_jelly.promotion.ports for the seams a composition root fills.
    - hivemind.royal_jelly.promotion.events for the trail events written.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from hivemind.common.errors import HiveMindError
from hivemind.common.logging import get_logger
from hivemind.pheromone import PheromoneEvent, PheromoneTrail
from hivemind.royal_jelly.errors import (
    PackageError,
    PromotionRefusedError,
    RoyalJellyError,
    SpecRejectedError,
)
from hivemind.royal_jelly.promotion.events import (
    ToolIdentity,
    promoted_event,
    quarantine_requested_event,
    quarantined_event,
    rejected_event,
    requested_event,
    scaffolded_event,
)
from hivemind.royal_jelly.promotion.ports import Announcer, HoneyDeposit, SandboxRunner
from hivemind.royal_jelly.registry import CombRegistry, Principal, PromotionOffer, RegistryChange
from hivemind.royal_jelly.scaffold import (
    Scaffolder,
    ScaffoldFailedError,
    ScaffoldResult,
    ToolRequestBrief,
)
from hivemind.royal_jelly.spec import TOOL_MODULE, QuarantineReport, ToolSpec
from hivemind.royal_jelly.tool_state import ToolState, transition
from waggle.clock import Clock
from waggle.errors import WaggleError
from waggle.ids import ToolId
from waggle.messages.reports import PlatformReport
from waggle.messages.tool.authoring import QuarantinePath

DEFAULT_MAX_RETRIES = 1  # Roadmap 9.6: one retry with the report, then give up.
MAX_PROMOTION_REASON_CHARS = 1_000  # The registry's bound on a promotion reason.

# Everything a scaffold can raise on purpose; each becomes a rejection at the `scaffold` stage.
_SCAFFOLD_FAILURES = (SpecRejectedError, PackageError, ScaffoldFailedError)
# What a post-promotion side effect (Honey, the announcement) may fail with without undoing it.
_AFTER_PROMOTION_FAILURES = (HiveMindError, WaggleError, OSError, TimeoutError)

type PathDecider = Callable[[PlatformReport], QuarantinePath]

__all__ = [
    "DEFAULT_MAX_RETRIES",
    "PathDecider",
    "PromotionFlow",
    "PromotionOutcome",
    "PromotionPorts",
    "sandbox_cell_path",
]

log = get_logger(__name__)


def sandbox_cell_path(platform: PlatformReport) -> QuarantinePath:
    """Choose the sandbox Cell route for every platform: the default `PathDecider`.

    Args:
        platform: The target platform (unused: the default never takes the real-Cell leg).

    Returns:
        `SANDBOX_CELL`. The composition root replaces this with the Comb's own path decision
        (`hivemind.royal_jelly.quarantine_comb.decide_path`), which sends a non-Ubuntu target
        through the real-Cell leg.
    """
    del platform  # The default ignores it; a real decider reads the distribution.
    return QuarantinePath.SANDBOX_CELL


@dataclass(frozen=True, slots=True)
class PromotionPorts:
    """The five collaborators a flow calls, grouped to keep its constructor short (rules 5.1)."""

    scaffolder: Scaffolder  # Writes the candidate tool.
    runner: SandboxRunner  # Proves it in a sandbox and returns the report.
    registry: CombRegistry  # The only way to a promoted row.
    honey: HoneyDeposit  # Describes the promotion to the Honey Store.
    announcer: Announcer  # Tells the Wardens whose Cells can use it.
    path_for: PathDecider = sandbox_cell_path  # Which quarantine route a platform takes.


@dataclass(frozen=True, slots=True)
class PromotionOutcome:
    """How one run ended: the final state and everything produced on the way."""

    state: ToolState  # PROMOTED or REJECTED.
    spec: ToolSpec | None  # The last scaffolded spec; None when nothing was ever scaffolded.
    report: QuarantineReport | None  # The last report the runner returned, if any.
    change: RegistryChange | None  # The registry change when promoted, else None.
    reason: str = ""  # Why it was rejected; empty when promoted.
    attempts: int = 0  # How many scaffolds were made (1, or 2 with the retry).


@dataclass(frozen=True, slots=True)
class _Progress:
    """What a run had produced when it ended: how many scaffolds, the last spec and report."""

    attempts: int = 0
    spec: ToolSpec | None = None
    report: QuarantineReport | None = None


@dataclass(frozen=True, slots=True)
class _Scaffolded:
    """What one attempt produced, carried to the next stage (rules 5.1: one argument group)."""

    result: ScaffoldResult
    attempt: int


class PromotionFlow:
    """Walk one requested tool to PROMOTED or REJECTED, recording a `tool.*` event per stage."""

    def __init__(
        self,
        ports: PromotionPorts,
        trail: PheromoneTrail,
        identity: ToolIdentity,
        clock: Clock,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        """Wire the flow to its ports, trail, identity and clock.

        Args:
            ports: Scaffolder, sandbox runner, registry, Honey deposit and announcer.
            trail: Where every `tool.*` event is recorded.
            identity: The Hive, node and actor stamped on those events.
            clock: Injected time source for event ids and timestamps.
            max_retries: Extra scaffolds after a failing report (0 disables the retry).
        """
        self._ports = ports
        self._trail = trail
        self._identity = identity
        self._clock = clock
        self._max_retries = max(0, max_retries)

    async def run(
        self, brief: ToolRequestBrief, principal: Principal, reviewed: bool = False
    ) -> PromotionOutcome:
        """Take `brief` from request to promotion or rejection.

        Args:
            brief: What to build; its capabilities are the most the tool may declare.
            principal: Who promotes: the Queen at HIVE scope, the owning Warden at CELL scope.
            reviewed: Whether the Queen's awake review accepted a real-Cell-path report (9.3a).

        Returns:
            The outcome. A rejection is a normal result, not an exception.
        """
        await self._record(requested_event(self._identity, self._clock, brief))
        state = ToolState.REQUESTED
        current = brief
        progress = _Progress()
        # One iteration per scaffold: the first, and at most `max_retries` more after a failure.
        while True:
            progress = _Progress(progress.attempts + 1, progress.spec, progress.report)
            try:
                scaffolded = await self._scaffold(current, progress.attempts)
            except _SCAFFOLD_FAILURES as exc:
                why = ("scaffold", exc.code, str(exc))
                return await self._reject(state, brief.tool_id, why, progress)
            spec = scaffolded.result.spec
            progress = _Progress(progress.attempts, spec, progress.report)
            state = transition(transition(state, ToolState.SCAFFOLDED), ToolState.QUARANTINING)
            try:
                report = await self._quarantine(brief, scaffolded)
            except RoyalJellyError as exc:
                why = ("quarantine", exc.code, str(exc))
                return await self._reject(state, brief.tool_id, why, progress)
            progress = _Progress(progress.attempts, spec, report)
            if report.passed:
                return await self._promote(state, scaffolded, report, (principal, reviewed))
            if progress.attempts > self._max_retries:
                why = ("quarantine", report.failing_gate or "unknown", _gate_reason(report))
                return await self._reject(state, brief.tool_id, why, progress)
            current = brief.with_retry(report, _source_of(scaffolded.result))

    async def _scaffold(self, brief: ToolRequestBrief, attempt: int) -> _Scaffolded:
        """Scaffold `brief` and record `tool.scaffolded` with the package's hash and size."""
        result = await self._ports.scaffolder.scaffold(brief)
        package = (result.package_sha256, result.package_size_bytes, attempt)
        await self._record(scaffolded_event(self._identity, self._clock, result.spec, package))
        return _Scaffolded(result=result, attempt=attempt)

    async def _quarantine(
        self, brief: ToolRequestBrief, scaffolded: _Scaffolded
    ) -> QuarantineReport:
        """Ask the runner to prove the package, recording the request and the verdict."""
        spec = scaffolded.result.spec
        await self._record(
            quarantine_requested_event(self._identity, self._clock, spec, scaffolded.attempt)
        )
        path = self._ports.path_for(brief.platform)
        # External await: the broker ships the blob and a sandbox Warden runs six gates; seconds
        # to minutes. The runner bounds itself; its failure is a rejection, never a hang here.
        report = await self._ports.runner.quarantine(spec, scaffolded.result.package, path)
        await self._record(quarantined_event(self._identity, self._clock, report))
        return report

    async def _promote(
        self,
        state: ToolState,
        scaffolded: _Scaffolded,
        report: QuarantineReport,
        authority: tuple[Principal, bool],
    ) -> PromotionOutcome:
        """Promote a passing report through the registry; a refusal is a rejection."""
        principal, reviewed = authority
        result = scaffolded.result
        spec = result.spec
        offer = PromotionOffer(result.package_sha256, result.package_size_bytes, reviewed)
        reason = f"Passed the Quarantine Comb on the {report.path.value} path."
        try:
            change = await self._ports.registry.promote(spec, report, offer, principal, reason)
        except PromotionRefusedError as exc:
            # The registry's refusals are final (no force flag); the stable code is the reason.
            why = ("promotion", exc.code, str(exc))
            progress = _Progress(scaffolded.attempt, spec, report)
            return await self._reject(state, spec.tool_id, why, progress)
        state = transition(state, ToolState.PROMOTED)
        await self._record(promoted_event(self._identity, self._clock, change))
        await self._after_promotion(spec, report, result.package_sha256, change)
        return PromotionOutcome(
            state=state, spec=spec, report=report, change=change, attempts=scaffolded.attempt
        )

    async def _after_promotion(
        self, spec: ToolSpec, report: QuarantineReport, digest: str, change: RegistryChange
    ) -> None:
        """Deposit Nectar and announce; either failing is logged and never undoes the promotion."""
        try:
            # External await: a local Honey intake or a Waggle send; seconds at worst.
            await self._ports.honey.deposit_promotion(spec, report, digest)
        except _AFTER_PROMOTION_FAILURES as exc:
            log.warning("royal_jelly.honey_deposit_failed", tool_id=spec.tool_id, error=repr(exc))
        try:
            await self._ports.announcer.announce(change, spec)
        except _AFTER_PROMOTION_FAILURES as exc:
            log.warning("royal_jelly.announce_failed", tool_id=spec.tool_id, error=repr(exc))

    async def _reject(
        self, state: ToolState, tool_id: ToolId, why: tuple[str, str, str], progress: _Progress
    ) -> PromotionOutcome:
        """End the run as REJECTED from `state`, recording `tool.rejected`."""
        # The edge is checked even though every caller is on a legal one: a future caller that is
        # not (a REJECTED tool rejected again) fails loudly instead of recording a second ending.
        transition(state, ToolState.REJECTED)
        await self._record(rejected_event(self._identity, self._clock, tool_id, why))
        return PromotionOutcome(
            state=ToolState.REJECTED,
            spec=progress.spec,
            report=progress.report,
            change=None,
            reason=f"{why[0]}: {why[1]}",
            attempts=progress.attempts,
        )

    async def _record(self, event: PheromoneEvent) -> None:
        """Append one event to the trail (local SQLite or memory; bounded by its busy timeout)."""
        await self._trail.record(event)


def _gate_reason(report: QuarantineReport) -> str:
    """Return the failing gate's one-sentence reason, or a generic line when it gave none."""
    failed = next((gate for gate in report.gates if not gate.passed), None)
    if failed is None or not failed.reason:
        return "the Quarantine Comb did not pass the tool"
    return failed.reason


def _source_of(result: ScaffoldResult) -> str:
    """Return the scaffold's `tool.py` as text for the retry brief (undecodable bytes replaced)."""
    return result.files[TOOL_MODULE].decode("utf-8", errors="replace")
