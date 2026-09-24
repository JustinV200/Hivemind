"""Define ModelJudgeReviewer: the model-backed JudgeReviewer a Warden's CappingGate calls through.

Roadmap step 4.10: "an independent review on ModelSlot.JUDGE with no shared context with the
proposing bee." `hivemind.supervision.capping.checks.judge.JudgeReviewer` is the Protocol seam that
package ships (it never imports `hivemind.llm`, an autopilot boundary, codingrules section 4);
`ModelJudgeReviewer` is the Warden-layer implementation that actually calls a model, satisfying it
structurally (codingrules section 8.1). `review` builds a prompt from the tier's rubric text and the
`JudgeRequest` alone -- the proposal's action and postconditions, never the proposer's transcript or
hot state (a bee's own always-loaded, bounded slice of memory) -- and runs it through
`hivemind.llm.complete_structured` on the injected `BoundModel` (a `ModelSlot` resolved to a live
provider and model, here always `ModelSlot.JUDGE`) through the Warden's own `CallGate` (the seat
meter every model call passes through). The model's own reply is parsed into a small private
schema (`_JudgeModelOutput`) rather than `JudgeVerdict` itself, because `JudgeVerdict.rubric_id`
must name the rubric this review actually ran against -- a fact this module already knows from
`request.rubric` -- never a value the model could misstate or omit.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside `hivemind.wardens`. Built
    by `hivemind.cli.compose.deps.build_warden_deps`, which resolves `ModelSlot.JUDGE` to a
    `BoundModel` and hands it here, then registers this reviewer into `WardenDeps.checks` (the
    `CheckKind.JUDGE` entry) through `hivemind.supervision.capping.judge_checks` and onto
    `WardenDeps.judge_reviewer` for
    `hivemind.wardens.spawn.audited_gate.AuditingCappingGate`'s own after-the-fact sampling. Calls
    into `hivemind.llm` (BoundModel, CallGate, LLMRequest, MalformedOutputError, Message,
    PromptName, Role, SectionLabel, complete_structured, render), `hivemind.supervision.capping`
    (JudgeAnswerError, JudgeOutcome, JudgeRequest, JudgeReviewer, JudgeVerdict) and waggle only.

Key invariants:
    - `review`'s prompt carries only `request`'s own fields (risk tier, action, acceptance criteria,
      rubric text) and never the proposer's id, transcript or hot state -- the "no shared context"
      rule (codingrules section 8.12).
    - The returned `JudgeVerdict.rubric_id` is always `request.rubric.rubric_id`, never a value the
      model produced: a hallucinated or mistyped id would silently misattribute every later audit
      finding (`hivemind.supervision.capping.audit`) to the wrong rubric version.
    - `review` never accumulates a transcript across calls (codingrules section 4): every call
      builds its `LLMRequest` fresh from `request` alone, matching every other awake-episode-style
      call in the Hive (codingrules section 8.8).
    - `review` translates `hivemind.llm.errors.MalformedOutputError` into `JudgeAnswerError`
      (this layer's own error) rather than letting it propagate; `ProviderUnavailableError` and
      `RateLimitedError` are never caught here, because those are outages Clustering handles, not
      an answer failure. See `hivemind.supervision.capping.checks.judge.JudgeCheck.run`, the
      catcher that turns `JudgeAnswerError` into a FAILED check outcome instead of a Worker crash.

See Also:
    - .claude/codingrules.md section 8.12 for "the judge is independent" and "no shared context".
    - .claude/roadmap.md step 4.10 for this module's own deliverable, verbatim.
    - hivemind.supervision.capping.checks.judge for JudgeReviewer, JudgeRequest, JudgeVerdict and
      JudgeOutcome, the seam and shapes this module implements against, and JudgeCheck.run, which
      catches the JudgeAnswerError this module raises.
    - hivemind.supervision.capping.errors for JudgeAnswerError itself.
    - hivemind.memory.compact.run for compact, the sibling ModelSlot.RIPENER call this module's
      shape (render a prompt fresh, call complete_structured, translate the result) mirrors.
    - hivemind.llm.prompts.judge_review for the prompt body this module renders.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field

from hivemind.forage.tempo import Tempo
from hivemind.llm import (
    BoundModel,
    CallGate,
    LLMRequest,
    MalformedOutputError,
    Message,
    PromptName,
    Role,
    SectionLabel,
    complete_structured,
    render,
)
from hivemind.supervision.capping import JudgeAnswerError, JudgeOutcome, JudgeRequest, JudgeVerdict
from waggle.messages.capping import ActionKind, ProposedAction

# The output budget for the one structured call this module makes. A verdict is short, but a
# reasoning model's thinking counts against this too: at 1024 the local 27B model spent every
# token thinking and returned nothing, nine times in a row (2026-09-21, the judge's first real
# run), so this leaves room to think. The manifest's per-slot max_output_tokens overrides it.
JUDGE_OUTPUT_TOKENS = 4_096
_ONE_LINE_INSTRUCTION = (
    "Score the action shown above against the rubric and postconditions, and return your verdict."
)
_MAX_REASON_CHARS = 500  # Mirrors JudgeVerdict's own per-reason bound.
_MAX_REASONS = 8  # Mirrors JudgeVerdict's own reasons-list bound.
_MAX_NOTES_CHARS = 2_000  # Mirrors JudgeVerdict's own notes bound.
# JudgeAnswerError's own detail is bounded here, not by MalformedOutputError (whose message can
# carry a large chunk of raw model output): a check's rejection reason is a tool-result string a
# Drone reads, never a full transcript (codingrules section 8.9-shaped budget, applied here).
_MAX_JUDGE_ERROR_DETAIL_CHARS = 500

__all__ = ["JUDGE_OUTPUT_TOKENS", "ModelJudgeReviewer"]


class _JudgeModelOutput(BaseModel):
    """The model's own structured reply: everything but the rubric id, which this module fills in.

    A private schema, never `JudgeVerdict` itself (module docstring: `rubric_id` must never come
    from the model).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: JudgeOutcome = Field(description="The judge's decision.")
    reasons: tuple[str, ...] = Field(
        default=(), max_length=_MAX_REASONS, description="Why, as a short list."
    )
    notes: str = Field(default="", description="Capped free text for anything else.")


class ModelJudgeReviewer:
    """A JudgeReviewer that runs `complete_structured` on `ModelSlot.JUDGE` through a CallGate."""

    def __init__(self, bound: BoundModel, lane_for: Callable[[Tempo], CallGate]) -> None:
        """Bind this reviewer to a JUDGE binding and the lane factory its calls run through.

        Args:
            bound: The `ModelSlot.JUDGE` binding to call; typically pinned to a different provider
                than `ModelSlot.WORKER` so blind spots do not correlate (codingrules section 8.12).
            lane_for: Builds the `CallGate` one review runs through, from the proposing task's
                own `Tempo` (`hivemind.llm.fanner.Fanner.lane` in production), so the judge's
                call queues at the task's urgency rather than a default one.
        """
        self._bound = bound
        self._lane_for = lane_for

    async def review(self, request: JudgeRequest) -> JudgeVerdict:
        """Score `request` against its rubric and return a structured verdict.

        Args:
            request: The proposal's content, tier, rubric and acceptance criteria; never the
                proposer's transcript or hot state (see `JudgeRequest`'s own docstring).

        Returns:
            The judge's verdict: approve, request changes or reject, with reasons.

        Raises:
            JudgeAnswerError: `complete_structured` exhausted every rung and fallback binding
                without ever producing parseable output (2026-09-21: nine attempts, empty reply,
                every time). Translated from `hivemind.llm.errors.MalformedOutputError` here,
                since `hivemind.supervision.capping.checks.judge.JudgeCheck.run` (the only
                catcher) never imports `hivemind.llm` (codingrules section 4).
        """
        llm_request = _build_request(self._bound, request)
        # Latency class: one model call, typically seconds to tens of seconds; the ladder's own
        # rung retries and fallback chain (hivemind.llm.complete_structured) cover a timeout or a
        # malformed reply, so no further retry logic lives here. ProviderUnavailableError and
        # RateLimitedError are deliberately NOT caught: those mean the bound provider (and every
        # fallback) is down, an outage hivemind.queen.cluster's Clustering rung pauses and resumes
        # the whole Hive for, never a single proposal's rejection -- they propagate unchanged.
        try:
            result = await complete_structured(
                self._bound, llm_request, _JudgeModelOutput, gate=self._lane_for(request.tempo)
            )
        except MalformedOutputError as exc:
            # The judge itself failed to answer, not a verdict on the proposal: JudgeCheck.run
            # turns this into a FAILED check outcome instead of letting it crash the Worker.
            raise JudgeAnswerError(str(exc)[:_MAX_JUDGE_ERROR_DETAIL_CHARS]) from exc
        output = result.value
        return JudgeVerdict(
            outcome=output.outcome,
            reasons=tuple(reason[:_MAX_REASON_CHARS] for reason in output.reasons[:_MAX_REASONS]),
            rubric_id=request.rubric.rubric_id,
            notes=output.notes[:_MAX_NOTES_CHARS],
        )


def _build_request(bound: BoundModel, request: JudgeRequest) -> LLMRequest:
    """Build the one LLMRequest `review` makes, assembled fresh from `request` alone every call."""
    system = render(
        PromptName.JUDGE_REVIEW, sections={SectionLabel.RETRIEVED: _render_request(request)}
    )
    return LLMRequest(
        slot=bound.slot,
        system=system,
        messages=(Message.text(Role.USER, _ONE_LINE_INSTRUCTION),),
        max_output_tokens=JUDGE_OUTPUT_TOKENS,
    )


def _render_request(request: JudgeRequest) -> str:
    """Render `request`'s own tier, action, postconditions and rubric as one RETRIEVED section."""
    lines = [
        f"Risk tier: {request.risk_tier.value}",
        f"Rubric ({request.rubric.rubric_id}): {request.rubric.text}",
        _render_action(request.action),
        "Postconditions:",
        *(
            f"- {pc.kind.value} on {pc.subject!r}: expected {pc.expected!r}"
            for pc in request.acceptance_criteria
        ),
    ]
    return "\n".join(lines)


def _render_action(action: ProposedAction) -> str:
    """Render one ProposedAction's own summary, kind and touched paths or command as one line."""
    if action.kind is ActionKind.COMMAND:
        detail = f"command={list(action.command)}"
    elif action.kind is ActionKind.DIFF:
        detail = f"paths={list(action.paths)}"
    else:
        detail = f"steps={list(action.steps)}"
    return f"Action ({action.kind.value}): {action.summary} [{detail}]"
