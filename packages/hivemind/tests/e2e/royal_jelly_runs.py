"""Script the Royal Jelly Lab's exit-criteria Hives: manifests, a scaffolder, a Worker that asks.

Roadmap phase 9's exit criteria are proved by whole Hives built with `build_hive` over a scripted
`FakeLLMProvider`: the Queen's planner, a Worker, the SCAFFOLDER and the judge all answer through
one responder, so a tool is requested, scaffolded, proved by the real Quarantine Comb (in the
development subprocess sandbox, or in a Virtual Cell's own in-process Warden), promoted and handed
to the Worker that asked, with nothing faked but the models. `LabScript` is that responder;
`count_lines_reply` is the one real tool every scenario scaffolds (it reads a scratch file through
`session.get_file` and returns its line count, with a `unittest` test that uses the runner's
`SESSION`); `lab_manifest` writes the manifest (a `[royal_jelly]` section, and a `[guard.roles.*]`
overlay that lets one Worker role ask for tools, since no role holds `tool:request` by default).

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by
    tests/e2e/test_phase9_exit_criteria*.py and tests/evals/scaffold.

Key invariants:
    - A scaffolder reply is a pydantic `ScaffoldReply` dumped to JSON, so the scripted tool passes
      the same schema the real SCAFFOLDER slot is held to.
    - The responder answers by slot, never by call count, except the SCAFFOLDER queue, which pops
      one reply per scaffold (a malicious scenario queues one per attempt).
    - Nothing here sleeps: callers wait on the trail with `kernel_helpers.wait_until`.

See Also:
    - tests.e2e.kernel_helpers for the scripting primitives reused here.
    - tests.e2e.honey_runs for the whole-Hive scripting this module follows.
"""

from __future__ import annotations

import json
import re
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from builders import virtual_cells as virtual_cell_builders
from builders.cli import ManifestTuning, fake_manifest
from builders.virtual_cells import ContainerSpawningFakeCellBackend
from e2e.kernel_helpers import (
    judge_approve_response,
    plan_response,
    text_response,
    tool_response,
    tool_round_count,
)

from hivemind.cell import HoneyClearance
from hivemind.cli.compose import GoalReport, Hive, build_hive, run_goal, run_hive
from hivemind.cli.in_cell.main import run_in_cell_warden as real_run_in_cell_warden
from hivemind.forage.slots import ModelSlot
from hivemind.llm import FakeLLMProvider, LLMRequest, LLMResponse, TextPart, ToolResultPart
from hivemind.manifest import HiveManifest, load_manifest
from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.royal_jelly.registry import CombToolRow
from hivemind.royal_jelly.scaffold import ScaffoldReply
from hivemind.wardens import WardenDeps
from waggle.clock import Clock, SystemClock

TOOL_NAME = "count_lines"
NOTES_NAME = "notes.txt"
NOTES_FILE = f"data/{NOTES_NAME}"
NOTES_TEXT = "alpha\nbeta\ngamma\n"
NOTES_LINES = 3
ANSWER_FILE = "answer.txt"
GOAL_TIMEOUT_S = 90.0  # Generous: a Comb run costs seconds; this bounds a hang.
HOST_DISTRIBUTION = "Ubuntu 24.04 LTS"  # Pinned so the quarantine path never depends on the host.
TOOL_SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string"}},
    "required": ["name"],
    "additionalProperties": False,
}
# A small real tool: it reads a file of the `data` directory only through the session it is
# handed. It stays inside one directory because a tool declaring a bare wildcard scope
# (`fs:read:**`) is refused by the Guard every time a Worker calls it.
TOOL_SOURCE = """\
from pathlib import Path


async def run(session, arguments):
    data = await session.get_file(Path("data") / arguments["name"])
    return {"lines": len(data.splitlines())}
"""
# The test writes its fixture straight into the sandbox scratch the runner hands it, then calls
# the tool through the runner's recording `SESSION`.
TEST_SOURCE = """\
import asyncio
import unittest
from pathlib import Path

from tool import run


class CountLinesTest(unittest.TestCase):
    def test_counts_the_lines_of_a_scratch_file(self):
        folder = Path(SESSION.scratch_dir) / "data"
        folder.mkdir()
        (folder / "notes.txt").write_text("a\\nb\\nc\\n")
        result = asyncio.run(run(SESSION, {"name": "notes.txt"}))
        self.assertEqual(result, {"lines": 3})
"""
# The capabilities the Worker asks the tool to need; the Forager and Drone roles both hold them.
TOOL_CAPABILITIES = ("fs:read:data", "fs:read:data/*")
_ROLE_SETS = {
    "drone": ("llm:worker",),
    "forager": ("llm:worker",),
}
_COMMON_ALLOW = (
    "fs:write:{scratch}/**",
    "fs:read:**",
    "exec:*",
    "tool:*",
    "tool:recall",
    "tool:remember",
    "spend:*",
    "question:human",
    "wax:propose",
    "honey:read:*",
    "honey:clearance:c2",
)
# The count a tool reports, whatever it names the key (`lines`, `line_count`): a model-written
# tool in the local eval chooses its own.
_FOUND_RE = re.compile(r'"[a-z_]*lines?[a-z_]*":\s*(\d+)')
ASK_DESCRIPTION = (
    "Count the lines of a text file in the data directory of my working directory. It takes one "
    "argument, `name`: the file name inside that directory, for example notes.txt. It returns an "
    'object such as {"lines": 3}.'
)

__all__ = [
    "ANSWER_FILE",
    "ASK_DESCRIPTION",
    "GOAL_TIMEOUT_S",
    "HOST_DISTRIBUTION",
    "NOTES_FILE",
    "NOTES_LINES",
    "NOTES_NAME",
    "NOTES_TEXT",
    "PROBE_WORDS",
    "TOOL_CAPABILITIES",
    "TOOL_NAME",
    "LabRun",
    "LabScript",
    "ProbeTurns",
    "append_lab",
    "ask_arguments",
    "build_lab_hive",
    "count_lines_reply",
    "events_of",
    "install_cell_script",
    "is_probe",
    "kinds_of",
    "lab_manifest",
    "pin_host_platform",
    "probe_plan",
    "probe_turn",
    "request_then_use_turn",
    "result_texts",
    "run_lab",
    "tool_names",
    "use_turn",
    "write_arguments",
]


def count_lines_reply(
    tool_source: str = TOOL_SOURCE,
    test_source: str = TEST_SOURCE,
    capabilities: Sequence[str] = TOOL_CAPABILITIES,
) -> dict[str, object]:
    """Build the JSON object the SCAFFOLDER slot answers with, validated as a `ScaffoldReply`.

    Args:
        tool_source: The whole `tool.py`.
        test_source: The whole `test_tool.py`.
        capabilities: The capability strings the spec declares.

    Returns:
        The reply as plain JSON-shaped data.
    """
    reply = ScaffoldReply(
        description="Count the lines of a file in the working directory and return the count.",
        input_schema_json=json.dumps(TOOL_SCHEMA),
        test_source=test_source,
        tool_source=tool_source,
        required_capabilities=tuple(capabilities),
    )
    return reply.model_dump(mode="json")


def structured_response(request: LLMRequest, payload: Mapping[str, object]) -> LLMResponse:
    """Answer a structured call with `payload` on whichever rung `request` is on."""
    body = json.dumps(payload)
    # NATIVE or JSON_MODE: the schema travelled with the request; PROMPTED: one fenced block.
    if request.response_schema is not None:
        return text_response(body)
    return text_response(f"```json\n{body}\n```")


class LabScript:
    """Answer every model call of a Royal Jelly Hive: plan, Worker turns, scaffolds, judge.

    `scaffolds` queues one scaffold reply per SCAFFOLDER call, in order (the last repeats); every
    SCAFFOLDER request is kept in `scaffold_requests` for assertions.
    """

    def __init__(
        self,
        worker_turn: Callable[[LLMRequest], LLMResponse],
        plans: Mapping[str, object] | Sequence[Mapping[str, object]],
        scaffolds: Sequence[Mapping[str, object]] = (),
    ) -> None:
        """Script a Hive.

        Args:
            worker_turn: Answers every WORKER-slot call.
            plans: The plan the QUEEN slot answers with, or one per goal in order (the last is
                reused once the list runs out).
            scaffolds: One reply per SCAFFOLDER call; the last is reused once the queue is out.
        """
        self._worker_turn = worker_turn
        self._plans = deque([plans] if isinstance(plans, Mapping) else plans)
        self._plan = self._plans[-1]
        self._scaffolds = deque(scaffolds)
        self._last: Mapping[str, object] | None = scaffolds[-1] if scaffolds else None
        self.scaffold_requests: list[LLMRequest] = []

    def responder(self, request: LLMRequest) -> LLMResponse:
        """Route one request by its slot; the one Responder a Royal Jelly Hive installs."""
        if request.slot is ModelSlot.QUEEN:
            plan = self._plans.popleft() if len(self._plans) > 1 else self._plan
            return plan_response(request, plan)
        if request.slot is ModelSlot.WORKER:
            return self._worker_turn(request)
        if request.slot is ModelSlot.SCAFFOLDER:
            return self._scaffold(request)
        if request.slot is ModelSlot.JUDGE:
            return judge_approve_response(request)
        return text_response("{}")

    def _scaffold(self, request: LLMRequest) -> LLMResponse:
        """Pop the next scaffold reply (the last one repeats) and answer in the right shape."""
        self.scaffold_requests.append(request)
        reply = self._scaffolds.popleft() if self._scaffolds else self._last
        assert reply is not None, "a SCAFFOLDER call arrived and no reply was scripted"
        return structured_response(request, reply)


def tool_names(request: LLMRequest) -> frozenset[str]:
    """Return the names of the tools a Worker request offers (native protocol)."""
    return frozenset(tool.name for tool in request.tools)


def result_texts(request: LLMRequest) -> list[str]:
    """Every tool result in the attempt's conversation, native or prompted, oldest first."""
    texts: list[str] = []
    for message in request.messages:
        for part in message.parts:
            if isinstance(part, ToolResultPart):
                texts.append(part.content)
            elif isinstance(part, TextPart) and "Result for " in part.text:
                texts.append(part.text)
    return texts


def request_then_use_turn(
    request: LLMRequest, *, scope: str = "hive", tool_name: str = TOOL_NAME
) -> LLMResponse:
    """The Worker script: write a file, ask for a tool, then (once offered) call it and answer.

    The script reads what the request offers rather than counting calls, because a promotion
    restarts the tool loop from state (a fresh conversation, codingrules 8.9) and the same script
    must carry on from there. Without the tool: round 0 writes `notes.txt`, round 1 calls
    `request_tool`, and a round after that means the request failed, so the script closes with
    the refusal it was given. With the tool offered: call it, write what it counted, close.
    """
    rounds = tool_round_count(request)
    if tool_name in tool_names(request):
        return use_turn(request, rounds, tool_name)
    if rounds == 0:
        return tool_response(
            request, (("notes", "write_file", write_arguments(NOTES_FILE, NOTES_TEXT)),)
        )
    if rounds == 1:
        return tool_response(request, (("ask", "request_tool", ask_arguments(tool_name, scope)),))
    if rounds == 2:
        content = "no tool arrived"
        return tool_response(
            request, (("answer", "write_file", write_arguments(ANSWER_FILE, content)),)
        )
    # The second result is the Hive's answer to `request_tool` (the first was the note's write).
    return text_response(f"No tool arrived: {result_texts(request)[1][:400]}")


def use_turn(request: LLMRequest, rounds: int, tool_name: str) -> LLMResponse:
    """Rounds once the tool is offered: call it on `notes.txt`, write the count, then close."""
    if rounds == 0:
        return tool_response(request, (("use", tool_name, _arguments_for(request, tool_name)),))
    count = found_count(request)
    if rounds == 1:
        content = f"lines: {count}"
        return tool_response(
            request, (("answer", "write_file", write_arguments(ANSWER_FILE, content)),)
        )
    return text_response(f"{NOTES_NAME} has {count} lines.")


def _arguments_for(request: LLMRequest, tool_name: str) -> dict[str, object]:
    """Arguments for the new tool, named by its own schema: its first required property."""
    for definition in request.tools:
        required = definition.parameters.get("required") if definition.name == tool_name else None
        if isinstance(required, list) and required and isinstance(required[0], str):
            return {required[0]: NOTES_NAME}
    return {"name": NOTES_NAME}


def found_count(request: LLMRequest) -> str:
    """Return the line count the new tool reported in this attempt's conversation, or `unknown`."""
    for text in reversed(result_texts(request)):
        match = _FOUND_RE.search(text)
        if match:
            return match.group(1)
    return "unknown"


def write_arguments(path: str, content: str) -> dict[str, object]:
    """Arguments for one `write_file` call."""
    return {"path": path, "content": content}


def ask_arguments(name: str, scope: str) -> dict[str, object]:
    """Arguments for the `request_tool` call that asks for the line counter."""
    return {
        "name": name,
        "description": ASK_DESCRIPTION,
        "required_capabilities": list(TOOL_CAPABILITIES),
        "scope": scope,
        "reason": "I must report how many lines data/notes.txt has and no tool does that.",
    }


def lab_manifest(
    tmp_path: Path,
    *,
    role: str = "drone",
    royal_jelly: str = "",
    tuning: ManifestTuning | None = None,
) -> Path:
    """Write a `fake_manifest` with a `[royal_jelly]` section and a role that may ask for tools.

    Args:
        tmp_path: The test's own tmp_path.
        role: The Worker role whose Guard set gains `tool:request` and `tool:scope:cell`.
        royal_jelly: The body of `[royal_jelly]`; the dev subprocess sandbox when empty.
        tuning: Forwarded to `fake_manifest`.

    Returns:
        The manifest path.
    """
    path = fake_manifest(tmp_path, tuning=tuning)
    append_lab(path, role=role, royal_jelly=royal_jelly)
    return path


def append_lab(path: Path, *, role: str, royal_jelly: str = "") -> None:
    """Append `[royal_jelly]` and the role overlay to an already-written manifest.

    Args:
        path: A manifest `fake_manifest` or `virtual_cells_manifest` wrote.
        role: The Worker role that may ask for tools.
        royal_jelly: The body of `[royal_jelly]`; the dev subprocess sandbox when empty.
    """
    body = royal_jelly or 'sandbox = "subprocess"\nrequest_timeout_s = 120\n'
    allow = ", ".join(f'"{cap}"' for cap in (*_COMMON_ALLOW, *_ROLE_SETS[role], *_TOOL_ASKING))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n[royal_jelly]\n{body}\n[guard.roles.{role}]\nallow = [{allow}]\n")


_TOOL_ASKING = ("tool:request", "tool:scope:cell")


def build_lab_hive(manifest_or_path: Path | HiveManifest, script: LabScript) -> Hive:
    """Build a whole Hive over a manifest (or its path) with `script` answering every model call."""
    manifest = (
        manifest_or_path
        if isinstance(manifest_or_path, HiveManifest)
        else load_manifest(manifest_or_path, {})
    )
    return build_hive(
        manifest, environ={}, clock=SystemClock(), responders={"fake": script.responder}
    )


def pin_host_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the Hive Stand's probed distribution to Ubuntu, so the sandbox path is the host's own."""
    monkeypatch.setattr(
        "hivemind.cell.local.probe._posix_distribution", lambda _system: HOST_DISTRIBUTION
    )


async def events_of(hive: Hive, family: str) -> list[PheromoneEvent]:
    """Every trail event of a family, oldest first."""
    return list(await hive.stores.trail.query(TrailQuery(family=family)))


async def kinds_of(hive: Hive, family: str) -> list[str]:
    """The kinds of every trail event of a family, oldest first."""
    return [event.kind for event in await events_of(hive, family)]


def install_cell_script(
    monkeypatch: pytest.MonkeyPatch, script: LabScript, deps_sink: list[WardenDeps] | None = None
) -> None:
    """Make every Virtual Cell a real in-process in-Cell Warden answering through `script`.

    The fake backend starts one in-Cell Warden per provisioned Cell
    (`ContainerSpawningFakeCellBackend`); this routes its WORKER, SCAFFOLDER and JUDGE slots to
    `script.responder`, so a Worker on a Cell, the Warden's own scaffold and the Capping judge
    are all answered by the scenario's script. `deps_sink` collects each Cell's `WardenDeps` (its
    resident Basket and trail among them) for assertions.
    """
    monkeypatch.setattr(
        "hivemind.cli.compose.virtual_cell_backends.FakeCellBackend",
        ContainerSpawningFakeCellBackend,
    )
    real = real_run_in_cell_warden

    async def scripted(
        environ: Mapping[str, str],
        clock: Clock,
        *,
        on_deps_built: Callable[[WardenDeps], None] | None = None,
    ) -> None:
        def hook(deps: WardenDeps) -> None:
            if on_deps_built is not None:
                on_deps_built(deps)
            provider = deps.bound.provider
            assert isinstance(provider, FakeLLMProvider)
            for slot in (ModelSlot.WORKER, ModelSlot.SCAFFOLDER, ModelSlot.JUDGE):
                provider.answer_slot(slot, script.responder)
            if deps_sink is not None:
                deps_sink.append(deps)

        await real(environ, clock, on_deps_built=hook)

    monkeypatch.setattr(virtual_cell_builders, "run_in_cell_warden", scripted)


@dataclass(frozen=True, slots=True)
class LabRun:
    """What running a Royal Jelly Hive's goals left: the reports, the tool trail, the registry."""

    reports: tuple[GoalReport, ...]
    tool_events: tuple[PheromoneEvent, ...]
    rows: tuple[CombToolRow, ...]
    extra: Any  # Whatever the scenario's `after` hook read while the Hive was still running.

    @property
    def tool_kinds(self) -> list[str]:
        """The kinds of every `tool.*` event, oldest first."""
        return [event.kind for event in self.tool_events]

    def of_kind(self, kind: str) -> list[PheromoneEvent]:
        """Every tool event of `kind`, oldest first."""
        return [event for event in self.tool_events if event.kind == kind]


async def run_lab(
    hive: Hive,
    goals: Sequence[str],
    *,
    before: Callable[[Hive], Awaitable[None]] | None = None,
    after: Callable[[Hive, tuple[GoalReport, ...]], Awaitable[Any]] | None = None,
) -> LabRun:
    """Run each goal in turn on a started Hive, then read the tool trail and the registry.

    Everything is read before the Hive stops, because its stores are closed afterwards.

    Args:
        hive: A Hive from `build_lab_hive`, not yet started.
        goals: The goal texts, submitted one after another (each finishes before the next).
        before: Awaited once the Hive is running, before the first goal is submitted.
        after: Called with the reports once every goal ended, still inside the running Hive; what
            it returns is `LabRun.extra`.

    Returns:
        The reports and everything the scenario asserts on.
    """
    reports: list[GoalReport] = []
    async with run_hive(hive):
        if before is not None:
            await before(hive)
        for goal in goals:
            reports.append(
                await run_goal(hive, goal, clearance=HoneyClearance.C1, timeout_s=GOAL_TIMEOUT_S)
            )
        extra = await after(hive, tuple(reports)) if after is not None else None
        access = hive.royal_jelly
        rows = await access.registry.list_tools() if access is not None else ()
        events = await events_of(hive, "tool")
    return LabRun(tuple(reports), tuple(events), tuple(rows), extra)


PROBE_WORDS = "Probe which tools are offered here"


def probe_plan() -> dict[str, object]:
    """Plan one task that must run on a Virtual Cell and only reports which tools it is offered."""
    return {
        "tasks": [
            {
                "key": "probe",
                "title": "Probe the tools of a Cell",
                "objective": f"{PROBE_WORDS} and write {ANSWER_FILE} under scratch.",
                "acceptance": [
                    {"kind": "FILE_EXISTS", "subject": ANSWER_FILE, "argv": [], "expected": None}
                ],
                "needs": {"isolation": "REQUIRED"},
                "clearance": "C1",
                "depends_on": [],
            }
        ]
    }


def is_probe(request: LLMRequest) -> bool:
    """Whether a Worker request belongs to the probe task (its brief carries `PROBE_WORDS`)."""
    texts = [request.system or ""]
    for message in request.messages:
        texts.extend(part.text for part in message.parts if isinstance(part, TextPart))
    return any(PROBE_WORDS in text for text in texts)


def probe_turn(request: LLMRequest, offered: list[frozenset[str]]) -> LLMResponse:
    """The probe task's script: note which tools this Worker is offered, write the answer, stop."""
    offered.append(tool_names(request))
    if tool_round_count(request) == 0:
        return tool_response(
            request, (("probe", "write_file", write_arguments(ANSWER_FILE, "probed")),)
        )
    return text_response("Probed the tools of this Cell.")


class ProbeTurns:
    """The Worker script of a two-goal scenario: the first goal asks for a tool, the second probes.

    `scope` is what the first goal's Worker asks for; `offered` collects the tools every probe
    Worker was offered, one set per model call.
    """

    def __init__(self, scope: str) -> None:
        """Script a first goal that asks for a tool of `scope` ("hive" or "cell")."""
        self._scope = scope
        self.offered: list[frozenset[str]] = []

    def turn(self, request: LLMRequest) -> LLMResponse:
        """Route a Worker call: the probe task reports its tools, any other asks for one."""
        if is_probe(request):
            return probe_turn(request, self.offered)
        return request_then_use_turn(request, scope=self._scope)
