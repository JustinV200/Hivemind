"""Review a hive-scope tool the Comb could only half prove: the Queen's awake judgement (step 9.3a).

When a tool targets a platform the sandbox cannot imitate, the Comb's static, schema and fuzz gates
run in the Ubuntu sandbox and the tests gate runs on a Real Cell of the target platform
(`QuarantinePath.REAL_CELL`, ADR-0047). The registry refuses to promote such a tool at hive scope
unless the Queen has reviewed it. `QueenReviewer` is the promotion flow's `Reviewer`: it runs one
stateless awake episode (`decide_awake`) with a trigger that states the tool's name, version,
platforms, capabilities and the gates that passed, and reads the one `QueenAction` it decides.
`QueenAction.RECORD` ("note it, nothing more to do") means accept; every other action, an
unavailable model or a failed episode means decline. The trigger carries no free text a requester
wrote: the description and the reason stay out of the prompt, so the review judges the proof and
the declared capabilities, not a requester's persuasion.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `queen.ticks.tools`. Called
    by `PromotionFlow` from a tool job (never from the tick, never from `queen.autopilot`); calls
    into `hivemind.queen.awake` (the episode), `hivemind.queen.cluster` (is the model reachable)
    and `hivemind.queen.trail`.

Key invariants:
    - The review runs only in a job beside the tick; autopilot never awaits it.
    - It declines whenever it cannot decide: no model (clustered), an LLM error, an overflowing
      prompt. A review that did not happen is never an approval.
    - Only `QueenAction.RECORD` approves.

See Also:
    - docs/adr/0047-quarantine-comb-gates-and-sandbox.md for the real-Cell path and the review.
    - hivemind.queen.ticks.wax for the same shape of episode on a Cell Wax proposal.
"""

from __future__ import annotations

from hivemind.cell import HoneyClearance
from hivemind.common.logging import get_logger
from hivemind.forage.slots import Effort
from hivemind.llm.errors import LLMError
from hivemind.memory import ContextOverflowError, TriggerEvent
from hivemind.queen.autopilot import QueenAction
from hivemind.queen.awake import QueenSources, decide_awake
from hivemind.queen.cluster import awake_available
from hivemind.queen.deps import QueenDeps
from hivemind.queen.human_inbox import HumanInbox
from hivemind.queen.trail import record_event
from hivemind.royal_jelly.spec import QuarantineReport, ToolSpec

REVIEW_EFFORT = (
    Effort.MEDIUM
)  # Rarer and weightier than routine traffic, like a Cell Wax judgement.
APPROVE = QueenAction.RECORD  # The decision that accepts; DENY_REQUEST (or any other) declines.
_REVIEW_FAILURES: tuple[type[Exception], ...] = (LLMError, ContextOverflowError)

log = get_logger(__name__)

__all__ = ["APPROVE", "REVIEW_EFFORT", "QueenReviewer", "review_trigger"]


def review_trigger(spec: ToolSpec, report: QuarantineReport) -> TriggerEvent:
    """Summarise a tool and its proof for the awake episode, without any requester-written text.

    Args:
        spec: The spec that passed.
        report: The passing report; its `path` is `REAL_CELL`.

    Returns:
        A `tool.review` trigger: ids, platforms, capabilities and gates, and what each decision
        means (RECORD accepts the tool; DENY_REQUEST declines it).
    """
    platforms = ", ".join(os.value for os in spec.supported_os)
    capabilities = ", ".join(spec.required_capabilities) or "none"
    gates = ", ".join(gate.name for gate in report.gates if gate.passed)
    summary = (
        f"Review hive-scope tool {spec.name} {spec.version} for promotion. It targets "
        f"{platforms} and acts under: {capabilities}. The Quarantine Comb ran on the "
        f"{report.path.value} path and passed these gates: {gates}. The sandbox could not "
        "imitate the target platform, so only you can accept it. Decide RECORD to accept the "
        "tool, or DENY_REQUEST to decline it; decide nothing else."
    )
    return TriggerEvent(
        kind="tool.review", summary=summary, payload_ref=spec.tool_id, clearance=HoneyClearance.C1
    )


class QueenReviewer:
    """Accept or decline a real-Cell-path, hive-scope tool by one awake episode (`Reviewer`)."""

    def __init__(self, deps: QueenDeps) -> None:
        """Bind the reviewer to the Queen's collaborators.

        Args:
            deps: The Queen's collaborators; the awake episode runs through `bound_for`.
        """
        self._deps = deps

    async def review(self, spec: ToolSpec, report: QuarantineReport) -> bool:
        """Run the review episode and return whether it accepted the tool.

        Args:
            spec: The spec that passed.
            report: The passing real-Cell-path report.

        Returns:
            True only when the episode decided `QueenAction.RECORD`; False when it decided
            anything else or could not run.
        """
        deps = self._deps
        if not awake_available(deps.cluster_state, deps):
            log.warning("queen.tools.review_unavailable", tool_id=spec.tool_id)
            return False
        sources = QueenSources(deps.chamber, deps.memory, HumanInbox())
        try:
            # External await: one model call through the ladder, seconds; it retries and falls
            # back along the binding's own chain itself, and this runs in a job, not the tick.
            decision = await decide_awake(
                deps, review_trigger(spec, report), sources, REVIEW_EFFORT
            )
        except _REVIEW_FAILURES as exc:
            log.warning("queen.tools.review_failed", tool_id=spec.tool_id, error=type(exc).__name__)
            return False
        approved = decision.action is APPROVE
        await record_event(
            deps,
            "queen.decided",
            spec.tool_id,
            action=decision.action.value,
            approved=approved,
        )
        return approved
