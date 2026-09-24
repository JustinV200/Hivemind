"""Judge one Guard Bee finding in an awake episode on the judge slot, shown its facts and no more.

A rule that asks for judgement (roadmap step 10.6, ADR-0035) hands each finding to one awake
episode (a bounded, stateless model call) before it is reported. `ModelGuardJudge` renders the
Guard review prompt (`PromptName.GUARD_REVIEW`) around the finding's facts and nothing else: the
rule, what it counts and measured, the kinds and number of the events it cites, the ids of what it
touches, the rule's own verdict and the actions its targets allow. No hot state, no transcript, no
content (the trail carries none), so the episode shares no context with any bee, like the Capping
judge (`hivemind.wardens.judge`) it mirrors. It runs on `ModelSlot.JUDGE`, which the manifest may
pin to another provider than `WORKER`, through the Queen's own call gate (the Royal Reserve's seats,
exactly as the House Bee's calls are charged), bounded by `[guard.bee] judge_timeout_s`. The reply
may raise or lower the confidence and change the action to `observe` or to a request its targets
allow; anything else, a timeout, or a provider outage leaves the rule's own verdict standing. The
judge only shapes a report: it never acts, and nothing it says reaches the trail but two enums.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.guard_bee`. Built by
    `.bee.build_guard_bee` and run by `.lane.JudgeLane` beside the Queen's tick. Calls into
    `hivemind.guard` (GuardAction, GuardConfidence), `hivemind.llm` (BoundModel, CallGate,
    LLMError, LLMRequest, Message, PromptName, Role, SectionLabel, complete_structured, render) and
    this package's `.evaluate` and `.rules`.

Key invariants:
    - The prompt carries ids, counts, kinds and enum values only (codingrules 12 and 8.12).
    - Every episode is built fresh from one finding; nothing is kept between episodes.
    - A judged action is always `observe` or a request its finding's targets allow: never a
      narrowing (a rule's alone) and never a target the finding does not name.
    - Every awaited model call is bounded by the timeout; on any failure the result is None.

See Also:
    - hivemind.llm.prompts.guard_review for the prompt body.
    - hivemind.wardens.judge for ModelJudgeReviewer, the shape this module mirrors.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from hivemind.common.logging import get_logger
from hivemind.guard import GuardAction, GuardConfidence
from hivemind.llm import (
    BoundModel,
    CallGate,
    LLMError,
    LLMRequest,
    Message,
    PromptName,
    Role,
    SectionLabel,
    complete_structured,
    render,
)
from hivemind.workers.roles.guard_bee.evaluate import Finding, targets_of
from hivemind.workers.roles.guard_bee.rules import GuardRule

# A verdict is two words, but a reasoning model's thinking counts against this too (the Capping
# judge's first real run needed room to think at all, hivemind.wardens.judge).
GUARD_JUDGE_OUTPUT_TOKENS = 2_048
_INSTRUCTION = "Judge the finding shown above: return a confidence and one of its allowed actions."

log = get_logger(__name__)

__all__ = [
    "GUARD_JUDGE_OUTPUT_TOKENS",
    "GuardJudge",
    "JudgeCase",
    "JudgeReply",
    "ModelGuardJudge",
    "Verdict",
    "render_case",
]


@dataclass(frozen=True, slots=True)
class Verdict:
    """What a report recommends and how sure it is: a rule's own, or a judge's change to it."""

    confidence: GuardConfidence
    action: GuardAction
    judged: bool = False  # True when an awake episode produced it.

    @classmethod
    def of(cls, rule: GuardRule) -> Verdict:
        """Return `rule`'s own deterministic verdict."""
        return cls(confidence=rule.confidence, action=rule.action)


@dataclass(frozen=True, slots=True)
class JudgeCase:
    """One finding waiting on judgement, and the actions its targets allow."""

    finding: Finding
    allowed: frozenset[GuardAction]


class JudgeReply(BaseModel):
    """The model's structured reply: a confidence and an action, nothing else."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    confidence: GuardConfidence = Field(description="How sure the Guard Bee should be.")
    action: GuardAction = Field(description="What the report should recommend.")


class GuardJudge(Protocol):
    """An awake episode over one finding; `ModelGuardJudge` in production, a fake in tests."""

    async def judge(self, case: JudgeCase) -> Verdict | None:
        """Return the judged verdict for `case`, or None when no judgement could be had.

        Args:
            case: The finding and the actions its targets allow.

        Returns:
            A verdict whose action is one of `case.allowed`, or None (the rule's own stands).
        """
        ...


class ModelGuardJudge:
    """A GuardJudge that runs one structured call on `ModelSlot.JUDGE` per finding."""

    def __init__(
        self, bound: Callable[[], BoundModel | None], gate: CallGate, timeout_s: float
    ) -> None:
        """Bind the judge to its slot resolver, its call gate and its time bound.

        Args:
            bound: Resolves `ModelSlot.JUDGE` for one episode; None when no binding exists.
            gate: The Queen's own call gate: the Royal Reserve's seats, never a grant's.
            timeout_s: The longest one episode may take, thinking and fallbacks included.
        """
        self._bound = bound
        self._gate = gate
        self._timeout_s = timeout_s

    async def judge(self, case: JudgeCase) -> Verdict | None:
        """Run one awake episode over `case`; see `GuardJudge.judge`."""
        bound = self._bound()
        if bound is None:
            return None  # No judge slot bound: the rule's own verdict stands.
        request = _request(bound, case)
        try:
            # Latency class: one model call, seconds to tens of seconds; the timeout bounds it
            # whole, and a timeout or an outage leaves the deterministic verdict standing.
            async with asyncio.timeout(self._timeout_s):
                result = await complete_structured(bound, request, JudgeReply, gate=self._gate)
        except (LLMError, TimeoutError) as exc:
            log.warning("guard_bee.judge_unavailable", error=type(exc).__name__)
            return None
        reply = result.value
        if reply.action not in case.allowed:
            # A narrowing, or a target the finding does not name: not the judge's to choose.
            log.warning("guard_bee.judge_action_refused", action=reply.action.value)
            return None
        return Verdict(confidence=reply.confidence, action=reply.action, judged=True)


def render_case(case: JudgeCase) -> str:
    """Render one finding's facts for the prompt: ids, counts, kinds and enum values only.

    Args:
        case: The finding and its allowed actions.

    Returns:
        A few labelled lines; nothing a bee read, wrote or said.
    """
    finding, rule = case.finding, case.finding.rule
    targets = targets_of(finding)
    kinds = Counter(sighting.fact.kind for sighting in finding.sightings)
    base = f" of {finding.base}" if finding.base is not None else ""
    lines = [
        f"Rule: {rule.key} ({rule.title})",
        f"Shape: {rule.shape.value}, grouped by {rule.group_by.value}, window {rule.window_s:g} s",
        f"Key: {finding.key}",
        f"Measured: {finding.measure:g}{base} against a threshold of {rule.threshold:g}",
        "Cited events: " + ", ".join(f"{kind} x{count}" for kind, count in sorted(kinds.items())),
        f"From {finding.sightings[0].fact.at.isoformat()} to {finding.through.isoformat()}",
        f"Cell: {targets.cell or 'none'}",
        f"Bees: {', '.join(targets.bees) or 'none'}",
        f"Tasks: {', '.join(targets.tasks) or 'none'}",
        f"Grants: {', '.join(targets.grants) or 'none'}",
        f"Rule's verdict: {rule.action.value} at {rule.confidence.value}",
        "Allowed actions: " + ", ".join(sorted(action.value for action in case.allowed)),
    ]
    return "\n".join(lines)


def _request(bound: BoundModel, case: JudgeCase) -> LLMRequest:
    """Build the one request an episode makes, fresh from `case` alone."""
    system = render(PromptName.GUARD_REVIEW, sections={SectionLabel.EVENT: render_case(case)})
    return LLMRequest(
        slot=bound.slot,
        system=system,
        messages=(Message.text(Role.USER, _INSTRUCTION),),
        max_output_tokens=GUARD_JUDGE_OUTPUT_TOKENS,
    )
