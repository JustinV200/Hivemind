"""Script the widget-port goal that the Honey e2e scenarios run twice through one Hive Stand Hive.

Roadmap phase 7's exit criteria and ADR-0034's label lowering are proven the same way: one goal
run twice on the Hive Stand (the machine the Queen runs on, a Real Cell, so borrowed), with the
House Bee's (the maintenance role's) whole pass driven between the runs, and the second run's
`TaskAssign` inspected for what the first run left in the Honey Store (the Hive's knowledge
base). `CompoundScript` answers every model call such a Hive makes through one scripted
`FakeLLMProvider`: the plan (QUEEN), a Drone (WORKER) that runs a discovery command only when the
Honey it was handed does not already say the port, the Capping judge's approval and, when a
scenario asks, the Ripener's structured summary with its own reading of the outcome's label
(RIPENER) and the clearance judge's verdict on lowering it (JUDGE). The two judges share
`ModelSlot.JUDGE`, so they are told apart by the prompt each request carries. `run_twice` runs the
goal, drives one House Bee pass (drain, ripen, file and review lowerings), runs it again and waits
for the second outcome's deposit.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by
    tests/e2e/test_honey_compounds.py.

Key invariants:
    - The Drone's script depends only on what its own prompt holds, so a run's Worker-call count
      says whether its Honey carried the port.
    - With no reading and no verdict scripted, the RIPENER answers `{}` (a heuristic summary, no
      reading) and the clearance judge is never needed: phase 7's own scenarios run unchanged.

See Also:
    - tests.e2e.kernel_helpers for the scripting primitives reused here.
    - docs/adr/0034-honey-label-lowering-is-a-judge-reviewed-proposal.md for the lowering flow.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from builders.cli import ManifestTuning, fake_manifest
from e2e.kernel_helpers import (
    judge_approve_response,
    plan_response,
    text_response,
    tool_response,
    tool_round_count,
    wait_until,
    write_call,
)

from hivemind.cell import HoneyClearance
from hivemind.cli.compose import GoalReport, Hive, build_hive, run_goal, run_hive
from hivemind.forage.slots import ModelSlot
from hivemind.llm import (
    LLMRequest,
    LLMResponse,
    PromptName,
    TextPart,
    ToolResultPart,
    load_prompt,
)
from hivemind.manifest import load_manifest
from hivemind.pheromone import PheromoneEvent, TrailQuery
from waggle.clock import SystemClock
from waggle.codec import Codec
from waggle.envelope import Envelope
from waggle.ids import TaskId
from waggle.messages.honey import HoneyHit
from waggle.messages.task import TaskAssign

GOAL = "Find which port the widget service listens on, and write it down."
PORT = "48213"
DISCOVERY_CALLS = 4  # Run the command, read its file, write the answer, close.
INFORMED_CALLS = 2  # Write the answer, close.
_OBJECTIVE = (
    "Find which port the widget service listens on and write that port number to answer.txt."
)
_FACT_TEXT = f"The widget service listens on port {PORT}."
_FACT_RE = re.compile(r"widget service listens on port (\d+)")
_DISCOVERY_FILE = "widget_service.txt"
_ANSWER_FILE = "answer.txt"
# The discovery command: what a Drone has to run when nothing it was handed says the port. The
# command writes its finding to scratch, since run_command reports only the Capping verdict.
_DISCOVER = f"open({_DISCOVERY_FILE!r}, 'w').write({_FACT_TEXT!r})"
_RETRIEVED_OPEN = "\n<<<retrieved>>>\n"  # The rendered section's own delimiter lines.
_RETRIEVED_CLOSE = "\n<<<end retrieved>>>"
_TIMEOUT_S = 20.0  # Generous: each run finishes in well under two seconds on this host.
_READING_REASON = "A service's port on a test machine; nothing about a person."

__all__ = [
    "DISCOVERY_CALLS",
    "GOAL",
    "INFORMED_CALLS",
    "PORT",
    "CompoundScript",
    "TwoRuns",
    "capture_assignments",
    "consulted",
    "from_first_run",
    "retrieved_port",
    "run_twice",
    "scripted_hive",
]


class CompoundScript:
    """Answer every model call in the Hive: plan, Drone rounds, Ripener reading, both judges.

    Every WORKER-slot call is counted, every QUEEN-slot (planning) prompt kept, and every
    clearance judge request kept, for the assertions.
    """

    def __init__(
        self, clearance: str, *, reading: str | None = None, verdict: str | None = None
    ) -> None:
        """Script a Hive whose plan runs at `clearance`.

        Args:
            clearance: The plan's task clearance, as its wire value ("C1", "C2").
            reading: The label the Ripener reads the outcome's text as ("C1"); None answers
                every RIPENER call with `{}`, a heuristic summary that records no reading.
            verdict: The clearance judge's answer ("APPROVE", "REJECT"); None leaves clearance
                reviews unscripted, for a scenario that never files a lowering.
        """
        self._plan = _plan(clearance)
        self._reading = reading
        self._verdict = verdict
        self.worker_calls = 0
        self.plan_prompts: list[str] = []
        self.clearance_reviews: list[LLMRequest] = []

    def responder(self, request: LLMRequest) -> LLMResponse:
        """Route one request by its slot; the one Responder a scripted Hive installs."""
        if request.slot is ModelSlot.QUEEN:
            self.plan_prompts.append(request.system or "")
            return plan_response(request, self._plan)
        if request.slot is ModelSlot.WORKER:
            self.worker_calls += 1
            return self._drone_turn(request)
        if request.slot is ModelSlot.JUDGE:
            return self._judge(request)
        # The RIPENER's summary: its own reading when scripted; else `{}`, on which the summary
        # falls back to a heuristic one (the verified outcome leads its content, so it keeps the
        # fact) and no reading is recorded.
        if self._reading is not None and _carries(request, PromptName.RIPEN_NECTAR):
            return _structured(request, _summary(self._reading))
        return text_response("{}")

    def _judge(self, request: LLMRequest) -> LLMResponse:
        """Answer the clearance judge with the scripted verdict, the Capping judge with APPROVE."""
        if self._verdict is not None and _carries(request, PromptName.JUDGE_CLEARANCE):
            self.clearance_reviews.append(request)
            return _structured(request, {"outcome": self._verdict, "reasons": ["Scripted."]})
        return judge_approve_response(request)

    def _drone_turn(self, request: LLMRequest) -> LLMResponse:
        """One Drone round: straight to the answer when told the port, else discover it first."""
        rounds = tool_round_count(request)
        told = retrieved_port(request.system or "")
        if told is not None:
            return _informed_turn(request, rounds, told)
        return _discovering_turn(request, rounds)


@dataclass(frozen=True, slots=True)
class TwoRuns:
    """What running the goal twice produced: both reports and each run's Worker calls."""

    first: GoalReport  # The first run, with nothing learned yet.
    second: GoalReport  # The second run, after one whole House Bee pass over the first's outcome.
    first_calls: int  # WORKER-slot calls the first run's Drone made.
    second_calls: int  # WORKER-slot calls the second run's Drone made.


def scripted_hive(
    tmp_path: Path,
    script: CompoundScript,
    capabilities: str = "full",
    tuning: ManifestTuning | None = None,
) -> Hive:
    """Build a whole Hive over the Hive Stand, real SQLite and `script`; outside any loop.

    Its manifest is `fake_manifest`'s own file, `tmp_path / "hive.toml"`, for a CLI to read.
    """
    manifest = load_manifest(fake_manifest(tmp_path, capabilities=capabilities, tuning=tuning), {})
    return build_hive(
        manifest, environ={}, clock=SystemClock(), responders={"fake": script.responder}
    )


async def run_twice(hive: Hive, script: CompoundScript, clearance: HoneyClearance) -> TwoRuns:
    """Run the goal, drive one whole House Bee pass over what it left, and run the goal again."""
    access, house_bee = hive.honey, hive.house_bee
    assert access is not None and house_bee is not None, "build_hive wired no Honey Store"
    async with run_hive(hive):
        first = await run_goal(hive, GOAL, clearance=clearance, timeout_s=_TIMEOUT_S)
        first_calls = script.worker_calls
        outcome_key = f"task_outcome:{first.tasks[0].id}"
        # The Queen deposits the outcome right after completing the task, a beat after run_goal
        # can already see it SUCCEEDED; wait for the deposit rather than race it.
        await wait_until(lambda: access.store.has_source(outcome_key))
        # The House Bee's own whole pass (drain, ripen, then file and review label lowerings),
        # driven here instead of waiting out its thirty-second pause.
        await house_bee.run_pass()
        second = await run_goal(hive, GOAL, clearance=clearance, timeout_s=_TIMEOUT_S)
        # The second outcome lands a beat later too; waiting for it keeps what a scenario reads
        # afterwards (a merge onto the first run's Nectar included) settled, never racing.
        await wait_until(lambda: access.store.has_source(f"task_outcome:{second.tasks[0].id}"))
    return TwoRuns(first, second, first_calls, script.worker_calls - first_calls)


def capture_assignments(monkeypatch: pytest.MonkeyPatch) -> list[TaskAssign]:
    """Record every TaskAssign put on the wire (the Queen's, and the Warden's copy to its bee)."""
    captured: list[TaskAssign] = []
    original = Codec.encode

    def encode(self: Codec, envelope: Envelope) -> bytes:
        if isinstance(envelope.payload, TaskAssign):
            captured.append(envelope.payload)
        return original(self, envelope)

    monkeypatch.setattr(Codec, "encode", encode)
    return captured


def from_first_run(assignments: list[TaskAssign], runs: TwoRuns) -> list[HoneyHit]:
    """Every hit the second run's TaskAssign carried that came from the first run's own task."""
    first_task = runs.first.tasks[0].id
    second_task = runs.second.tasks[0].id
    return [
        hit
        for assignment in assignments
        if assignment.task_id == second_task
        for hit in assignment.honey
        if hit.provenance.task_id == first_task
    ]


async def consulted(hive: Hive, task_id: TaskId) -> list[PheromoneEvent]:
    """The `queen.honey_consulted` events of the pre-check for `task_id`."""
    events = await hive.stores.trail.query(
        TrailQuery(kind="queen.honey_consulted", subject_id=task_id)
    )
    return [event for event in events if event.payload.get("stage") == "assign"]


def retrieved_port(system: str) -> str | None:
    """Return the port the prompt's RETRIEVED section states, or None when it states none."""
    start = system.find(_RETRIEVED_OPEN)
    if start == -1:
        return None
    section = system[start : system.find(_RETRIEVED_CLOSE, start)]
    match = _FACT_RE.search(section)
    return match.group(1) if match else None


def _plan(clearance: str) -> dict[str, object]:
    """The one-task plan both runs share, at `clearance`."""
    return {
        "tasks": [
            {
                "key": "widget-port",
                "title": "Find the widget service's port",
                "objective": _OBJECTIVE,
                "acceptance": [
                    {"kind": "FILE_EXISTS", "subject": _ANSWER_FILE, "argv": [], "expected": None}
                ],
                "needs": {},
                "clearance": clearance,
                "depends_on": [],
            }
        ]
    }


def _summary(reading: str) -> dict[str, object]:
    """The Ripener's structured summary of the outcome, labelling its text as `reading`."""
    return {
        "title": "Where the widget service listens",
        "summary": _FACT_TEXT,
        "key_facts": [_FACT_TEXT],
        "clearance": reading,
        "clearance_reason": _READING_REASON,
    }


def _carries(request: LLMRequest, prompt: PromptName) -> bool:
    """Whether `request` was rendered from `prompt`: its system prompt opens with that body."""
    return (request.system or "").startswith(load_prompt(prompt).rstrip("\n"))


def _structured(request: LLMRequest, payload: dict[str, object]) -> LLMResponse:
    """Answer a structured call with `payload`, on whichever rung `request` is on."""
    body = json.dumps(payload)
    # NATIVE or JSON_MODE: the schema travelled with the request; PROMPTED: one fenced block.
    if request.response_schema is not None:
        return text_response(body)
    return text_response(f"```json\n{body}\n```")


def _informed_turn(request: LLMRequest, rounds: int, port: str) -> LLMResponse:
    """Told the port by the Honey it was handed: write the answer, then close."""
    if rounds == 0:
        return tool_response(request, (write_call(_ANSWER_FILE, port),))
    return text_response(f"The widget service listens on port {port}.")


def _discovering_turn(request: LLMRequest, rounds: int) -> LLMResponse:
    """Not told: run the discovery command, read what it wrote, write the answer, then close."""
    if rounds == 0:
        return tool_response(
            request, (("discover", "run_command", {"argv": [sys.executable, "-c", _DISCOVER]}),)
        )
    if rounds == 1:
        return tool_response(request, (("read", "read_file", {"path": _DISCOVERY_FILE}),))
    # What the read reported, as this attempt's own conversation carries it.
    port = _found_port(request) or "unknown"
    if rounds == 2:
        return tool_response(request, (write_call(_ANSWER_FILE, port),))
    return text_response(f"The widget service listens on port {port}.")


def _found_port(request: LLMRequest) -> str | None:
    """Return the port a tool result in this attempt's own conversation reported, if any."""
    # Native tool results arrive as ToolResultParts, prompted ones as text; either may carry it.
    for message in request.messages:
        for part in message.parts:
            text = part.content if isinstance(part, ToolResultPart) else None
            if isinstance(part, TextPart):
                text = part.text
            match = _FACT_RE.search(text or "")
            if match:
                return match.group(1)
    return None
