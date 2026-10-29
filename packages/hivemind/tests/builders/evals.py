"""Build evaluation-harness values: a graded goal result, raw evidence, and a whole report.

`make_goal_result(passed=False)` returns a `GoalResult` with every column filled in with a plain,
passing default, so a test for the report or the measured-grade mapping states only the column it
cares about. `make_evidence` does the same for the raw `GoalEvidence` the graders read, and
`make_eval_report` assembles a report from results. `local_eval_manifest` points
docs/manifests/local.toml at the operator's own server and models, from the environment.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the evaluation harness
    tests under tests/unit/cli/llm/eval, tests/evals/routing and tests/evals/handoff.

Key invariants:
    - Defaults describe a clean, passing run of the first goal; an override changes only what it
      names, and every value is built through the real model so an invalid combination fails.

See Also:
    - hivemind.cli.llm.eval.grades for the models built here.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from builders.audio import SAMPLE_RATE, tone_pcm
from e2e.kernel_helpers import (
    HaikuScript,
    WorkerTurn,
    default_worker_turn,
    single_task_plan,
    text_response,
    tool_response,
    tool_round_count,
    write_call,
)

from hivemind.cli.compose import Hive
from hivemind.cli.llm.eval.evidence import GoalEvidence
from hivemind.cli.llm.eval.goals import EvalGoal
from hivemind.cli.llm.eval.grades import (
    AlarmGrade,
    DecompositionGrade,
    EffectsGrade,
    GoalResult,
    HandoffGrade,
    NeedsGrade,
    QuestionGrade,
    RunCost,
    RungGrade,
    ToolCallGrade,
)
from hivemind.cli.llm.eval.measured import MeasuredEntry, SlotScore
from hivemind.cli.llm.eval.report import BindingRow, EvalReport
from hivemind.llm import LLMRequest, LLMResponse, Responder
from hivemind.llm.transcription import encode_wav
from hivemind.manifest import HiveManifest, load_manifest
from hivemind.manifest.schema.llm import SlotBinding
from hivemind.supervision import Checkpoint

__all__ = [
    "ScriptedEval",
    "local_eval_manifest",
    "make_eval_report",
    "make_evidence",
    "make_goal_result",
    "scripted_responder",
    "write_fixture_clips",
]

_LOCAL_MANIFEST = Path(__file__).resolve().parents[4] / "docs" / "manifests" / "local.toml"
_LOCAL_PROVIDER = "local"  # The one provider docs/manifests/local.toml declares.
_FINAL_TEXT = "Done."  # The Drone's closing line once every tool call has landed.
_PRINT_42 = [sys.executable, "-c", "print(6 * 7)"]  # The command goal's one command.


def make_goal_result(**overrides: object) -> GoalResult:
    """Build a passing GoalResult for the 'haiku' goal; `overrides` replace any field."""
    fields: dict[str, object] = {
        "goal": "haiku",
        "succeeded": True,
        "timed_out": False,
        "error": None,
        "decomposition": DecompositionGrade(
            plan_parsed=True, task_count=1, without_acceptance=0, passed=True
        ),
        "needs": NeedsGrade(compared=5, mismatches=(), score=1.0, passed=True),
        "effects": EffectsGrade(expected=("a.txt",), missing=(), passed=True),
        "question": QuestionGrade(applicable=False, asked=False, answered=False, passed=None),
        "tool_calls": ToolCallGrade(model_calls=4, malformed=0, validity_rate=1.0),
        "rung": RungGrade(start="NATIVE", reached="NATIVE", stepped_down=False),
        "alarms": AlarmGrade(raised=0, escalated=0, rate=0.0),
        "handoff": HandoffGrade(applicable=False, checkpoints=0, resumed=0, passed=None),
        "cost": RunCost(steps=3, cost_usd=0.0, unpriced_calls=0, wall_time_s=1.5),
        "slot_calls": {"QUEEN": 1, "WORKER": 3},
        "passed": True,
    }
    fields.update(overrides)
    return GoalResult(**fields)


def make_evidence(**overrides: object) -> GoalEvidence:
    """Build a bare successful GoalEvidence for 'haiku'; `overrides` replace any field."""
    fields: dict[str, object] = {
        "goal": "haiku",
        "succeeded": True,
        "timed_out": False,
        "elapsed_s": 2.0,
    }
    fields.update(overrides)
    return GoalEvidence(**fields)  # type: ignore[arg-type]


def make_eval_report(**overrides: object) -> EvalReport:
    """Build an EvalReport over one passing goal, one slot score and one measured grade."""
    when = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
    fields: dict[str, object] = {
        "day": when.date(),
        "generated_at": when,
        "manifest": "hive.toml",
        "bindings": (BindingRow(key="worker", provider="fake", model="scripted-worker"),),
        "goals": (make_goal_result(),),
        "slot_scores": (SlotScore(slot="WORKER", score=0.9, goals=1),),
        "transcriber": None,
        "measured": (
            MeasuredEntry(
                source_id="fake_default",
                slots=("WORKER",),
                score=0.9,
                grade=5,
                evidence="hive llm eval: WORKER 0.90 over 1",
            ),
        ),
        "map_updated": False,
    }
    fields.update(overrides)
    return EvalReport(**fields)


def write_fixture_clips(directory: Path, transcripts: Mapping[str, str]) -> Path:
    """Write one `<name>.wav` tone and one `<name>.txt` reference per entry of `transcripts`.

    A tone says nothing, so the clip's content is irrelevant: the reference text is what a
    scripted fake transcriber is made to hear (or mishear) when the grader is proven. Each clip is
    a different length, so latency and real-time factor differ clip to clip.

    Args:
        directory: Where to write; created when missing.
        transcripts: Clip name to reference text.

    Returns:
        `directory`, ready to pass as `--fixtures`.
    """
    directory.mkdir(parents=True, exist_ok=True)
    for index, (name, reference) in enumerate(transcripts.items(), start=1):
        pcm = tone_pcm(seconds=0.25 * index, sample_rate=SAMPLE_RATE)
        (directory / f"{name}.wav").write_bytes(encode_wav(pcm, SAMPLE_RATE, 1))
        (directory / f"{name}.txt").write_text(reference, encoding="utf-8")
    return directory


# ──────────────────────────────────────────────────────────────────────────────
# Scripted fake providers, one per fixed goal
# ──────────────────────────────────────────────────────────────────────────────


def scripted_responder(goal: str, hive_box: list[Hive] | None = None) -> Responder:
    """Return the fake provider's script for one fixed goal: a plan, and a Drone that does it.

    Every script plans one task with a file-exists acceptance per expected file and has the Drone
    do exactly what the goal asks, so a harness run over the fake passes every column. The
    `checkpoint` script triggers its own mid-task checkpoint, the way the kernel's checkpoint
    scenario does, from inside the first worker call; it needs the Hive, handed in `hive_box`
    (a one-element list the test fills from `RunnerOptions.on_hive`).

    Args:
        goal: A goal name from the fixed set.
        hive_box: Holds the running Hive for the `checkpoint` script; unused by the others.

    Returns:
        A Responder to install on every `kind = "fake"` provider.
    """
    files = {
        "haiku": ("haiku_1.txt", "haiku_2.txt", "haiku_3.txt"),
        "command": ("answer.txt",),
        "ask": ("season.txt",),
        "checkpoint": tuple(f"part_{n}.txt" for n in range(1, 5)),
    }[goal]
    turns: dict[str, WorkerTurn] = {
        "haiku": default_worker_turn,
        "command": _command_turn,
        "ask": _ask_turn,
        "checkpoint": _checkpoint_turn(hive_box if hive_box is not None else []),
    }
    plan = single_task_plan(*files, key=goal, title=f"Eval goal {goal}")
    return HaikuScript(turns[goal], plan).responder


class ScriptedEval:
    """One fake provider script for a whole eval run: it follows whichever goal is running.

    `RunnerOptions` carries one set of responders for every goal, but each fixed goal needs its own
    script. `on_hive`, which the runner calls with each freshly built Hive, tells this which goal
    is current and hands the checkpoint script the Hive it must checkpoint; `responder` then
    answers every call with the current goal's script.
    """

    def __init__(self) -> None:
        """Build the four goal scripts, sharing one Hive box."""
        self._hive_box: list[Hive] = []
        self._current = "haiku"
        self._scripts = {
            name: scripted_responder(name, self._hive_box)
            for name in ("haiku", "command", "ask", "checkpoint")
        }

    def on_hive(self, goal: EvalGoal, hive: Hive) -> None:
        """Switch to `goal`'s script and remember its Hive (the runner's `on_hive` seam)."""
        self._current = goal.name
        self._hive_box[:] = [hive]

    def responder(self, request: LLMRequest) -> LLMResponse:
        """Answer one model call with the current goal's script."""
        return self._scripts[self._current](request)


def _command_turn(request: LLMRequest) -> LLMResponse:
    """Run one command, then write what it printed."""
    rounds = tool_round_count(request)
    if rounds == 0:
        return tool_response(request, (("cmd_1", "run_command", {"argv": _PRINT_42}),))
    if rounds == 1:
        return tool_response(request, (write_call("answer.txt", "42"),))
    return text_response(_FINAL_TEXT)


def _ask_turn(request: LLMRequest) -> LLMResponse:
    """Ask which season, then write the haiku."""
    rounds = tool_round_count(request)
    if rounds == 0:
        return tool_response(request, (("ask_1", "ask", {"text": "Which season?"}),))
    if rounds == 1:
        return tool_response(request, (write_call("season.txt", "leaves fall slowly"),))
    return text_response(_FINAL_TEXT)


def _checkpoint_turn(hive_box: list[Hive]) -> WorkerTurn:
    """Write four files, one per round, with a checkpoint scheduled on the very first call."""
    sent = {"done": False}
    pending: list[asyncio.Future[None]] = []  # Held so the task is not collected mid-flight.

    def turn(request: LLMRequest) -> LLMResponse:
        rounds = tool_round_count(request)
        if rounds == 0 and not sent["done"] and hive_box:
            sent["done"] = True
            hive = hive_box[0]
            # Fire-and-forget on the running loop; the Warden delivers it to the sub-bee at once.
            worker_id = hive.warden.sub_bees[0].worker_id
            pending.append(
                asyncio.ensure_future(hive.warden.intervene(worker_id, Checkpoint(reason="eval")))
            )
        if rounds < 4:
            return tool_response(request, (write_call(f"part_{rounds + 1}.txt", f"{rounds + 1}"),))
        return text_response(_FINAL_TEXT)

    return turn


def local_eval_manifest(environ: Mapping[str, str]) -> HiveManifest:
    """Load local.toml, pointed at the operator's own server and, optionally, its own models."""
    manifest = load_manifest(_LOCAL_MANIFEST)
    llm = manifest.llm
    providers = dict(llm.providers)
    providers[_LOCAL_PROVIDER] = providers[_LOCAL_PROVIDER].model_copy(
        update={"base_url": environ["HIVEMIND_LOCAL_LLM_BASE_URL"]}
    )
    slots = dict(llm.slots)
    # Either scored slot's model may be replaced for a server that hosts different ones.
    for key, variable in (
        ("queen", "HIVEMIND_LOCAL_QUEEN_MODEL"),
        ("worker", "HIVEMIND_LOCAL_WORKER_MODEL"),
    ):
        if environ.get(variable):
            slots[key] = SlotBinding(provider=_LOCAL_PROVIDER, model=environ[variable])
    return manifest.model_copy(
        update={"llm": llm.model_copy(update={"providers": providers, "slots": slots})}
    )
