"""Prove a Scout-then-Forager goal end to end through a real Queen, Warden and both roles.

Roadmap steps 6.9 and 6.10's own flow: the Queen plans a goal as a Scout followed by a Forager
that depends on it; the Scout, on the Hive Stand with a browser-only Exoskeleton, looks at the
fixture site's login page and files a report naming the form; the Queen carries that report down
as the Forager's recon; the Forager's brief shows it, and the Forager logs in, its URL_MATCHES and
ELEMENT_TEXT acceptance checked by its Warden on the attached browser. An infeasible Scout, in
the second scenario, fails without retry and cancels the Forager with its reason. Everything but
the model is real: the Queen's planner and dispatcher, the Warden's spawn, attach, Capping gate
and acceptance, and the roles' tool loops. The model is a scripted FakeLLMProvider, and the browser
is the fake one serving `login_site()` on a loopback origin, so this runs in CI with no extras.

Fits into the Hive:
    Test infrastructure (codingrules section 14), not shipped. Exercises `hivemind.queen`
    (planner, dispatcher, results), `hivemind.wardens` (spawn, equip, acceptance) and
    `hivemind.workers.roles` (Scout, Forager, worker_for).

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests.e2e.kernel_helpers for build_cluster_pair, HaikuScript and the scripting helpers.
    - tests.unit.workers.roles.test_scout_then_forager for the same flow at the Worker level.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping

import pytest
from builders.cells import make_capabilities, make_cell
from e2e.kernel_helpers import (
    ClusterPair,
    HaikuScript,
    PairOptions,
    build_cluster_pair,
    text_response,
    tool_response,
    tool_round_count,
    wait_until,
)

from hivemind.brood_chamber import Task, TaskFilter, TaskStatus, is_terminal
from hivemind.cell import CellKind, HoneyClearance
from hivemind.exoskeleton.browser.fake import (
    LOGIN_HEADING,
    LOGIN_PASSWORD,
    LOGIN_USERNAME,
    WELCOME_HEADING,
    FakeBrowserLauncher,
    login_site,
)
from hivemind.exoskeleton.recorder import InMemoryRecordingStore
from hivemind.llm import LLMRequest, LLMResponse, Message, TextPart
from hivemind.memory import BeeBread, BeeBreadEntryKind
from hivemind.queen.deps import QueenDeps
from hivemind.workers.roles import worker_for
from waggle.clock import SystemClock
from waggle.messages.capping import ElementTarget
from waggle.messages.task import SCOUT_REPORT_FILE

pytestmark = pytest.mark.e2e

_ORIGIN = "http://127.0.0.1:8000"  # A loopback origin: the fake site stays on the Cell.
_FORM_TARGET = f"{_ORIGIN}/login"  # What the Scout reports; the Forager must see it in its brief.
_FORM_FINDING = "The login form has a Username field, a Password field and a Log in button."
_TIMEOUT_S = 15.0  # Both tasks finish in well under a second; this bounds a hung dispatch.
_BROWSER_NEED = {"exoskeleton": True, "browser_only": True}
_GREETING = ElementTarget(role="heading", name=WELCOME_HEADING)

Turn = Callable[[LLMRequest], LLMResponse]
Call = tuple[str, str, Mapping[str, object]]  # One scripted tool call: id, tool name, arguments.


def _plan(_goal: str) -> dict[str, object]:
    """Plan the login as a Scout, then a Forager that depends on it (roadmap 6.9, 6.10)."""
    report: dict[str, object] = {
        "kind": "FILE_EXISTS",
        "subject": SCOUT_REPORT_FILE,
        "argv": [],
        "expected": None,
    }
    welcome = {
        "kind": "URL_MATCHES",
        "subject": "page",
        "argv": [],
        "expected": f"{_ORIGIN}/welcome*",
    }
    greeted = {
        "kind": "ELEMENT_TEXT",
        "subject": _GREETING.subject(),
        "argv": [],
        "expected": WELCOME_HEADING,
    }
    common = {"needs": _BROWSER_NEED, "clearance": "C1"}
    return {
        "tasks": [
            {
                "key": "scout",
                "title": "Scout the login page",
                "objective": f"Look at {_ORIGIN}/login and report what its form needs.",
                "acceptance": [report],
                "role": "SCOUT",
                "depends_on": [],
                **common,
            },
            {
                "key": "login",
                "title": "Log in",
                "objective": f"Log in at {_ORIGIN}/login as {LOGIN_USERNAME}, password "
                f"{LOGIN_PASSWORD} (a secret: type it with secret true).",
                "acceptance": [welcome, greeted],
                "role": "FORAGER",
                "depends_on": ["scout"],
                **common,
            },
        ]
    }


def _scout_turn(feasible: bool) -> Turn:
    """Script the Scout: navigate, snapshot, then file its report."""
    report = {
        "feasible": feasible,
        "summary": "Looked at the login page." if feasible else "The login page is unusable.",
        "findings": [_FORM_FINDING],
        "suggested_steps": ["Fill Username", "Fill Password as a secret", "Click Log in"],
        "risks": [],
        "targets": [_FORM_TARGET],
    }
    rounds: tuple[Call, ...] = (
        ("nav", "browser_navigate", {"url": _FORM_TARGET}),
        ("look", "browser_snapshot", {}),
        ("report", "report_findings", report),
    )

    def turn(request: LLMRequest) -> LLMResponse:
        return tool_response(request, (rounds[min(tool_round_count(request), 2)],))

    return turn


def _forager_turn(briefs: list[str]) -> Turn:
    """Script the Forager: log in, read the page it lands on, then stop; keep every brief."""
    password = {"target": {"label": "Password"}, "text": LOGIN_PASSWORD, "secret": True}
    submit = {"target": {"role": "button", "name": LOGIN_HEADING}}
    rounds: tuple[Call, ...] = (
        ("nav", "browser_navigate", {"url": _FORM_TARGET}),
        ("user", "browser_fill", {"target": {"label": "Username"}, "text": LOGIN_USERNAME}),
        ("pass", "browser_fill", password),
        ("go", "browser_click", {**submit, "expect": {"url": f"{_ORIGIN}/welcome*"}}),
        ("read", "browser_read", {}),
    )

    def turn(request: LLMRequest) -> LLMResponse:
        briefs.extend(text for message in request.messages for text in _texts(message))
        done = tool_round_count(request)
        if done < len(rounds):
            return tool_response(request, (rounds[done],))
        return text_response("Logged in; the welcome page greets the user.")

    return turn


def _worker_turn(scout: Turn, forager: Turn) -> Turn:
    """Route a Worker call to the role asking: only a Scout is offered report_findings."""

    def turn(request: LLMRequest) -> LLMResponse:
        names = {tool.name for tool in request.tools}
        return scout(request) if "report_findings" in names else forager(request)

    return turn


async def _pair(worker: Turn) -> ClusterPair:
    """Wire a real Queen and Warden over a fake-site browser, scripted with `worker`."""
    clock = SystemClock()
    script = HaikuScript(worker, plan=_plan(""))
    capabilities = make_capabilities(has_browser=True)
    cell = make_cell(kind=CellKind.REAL, clock=clock, capabilities=capabilities)
    overrides = {
        "browser_launcher": FakeBrowserLauncher(login_site(_ORIGIN), clock),
        "recording_store": InMemoryRecordingStore(),
    }
    options = PairOptions(responder=script.responder, cell=cell, warden_overrides=overrides)
    return await build_cluster_pair(clock, _plan(""), worker_for, pair_options=options)


async def _run_goal(worker: Turn) -> QueenDeps:
    """Run the Scout-then-Forager goal until every task of it is terminal; return the deps."""
    pair = await _pair(worker)
    await pair.warden.start()
    queen_task = asyncio.ensure_future(pair.queen.run())
    warden_task = asyncio.ensure_future(pair.warden.run())
    try:
        await pair.queen.submit_goal("Log in to the fixture site.", clearance=HoneyClearance.C1)
        await wait_until(lambda: _all_terminal(pair.deps), timeout_s=_TIMEOUT_S)
    finally:
        await pair.queen.stop()
        await pair.warden.stop()
        await asyncio.wait_for(queen_task, timeout=5.0)
        await asyncio.wait_for(warden_task, timeout=5.0)
    return pair.deps


async def _all_terminal(deps: QueenDeps) -> bool:
    """Whether both tasks of the goal have finished, one way or another."""
    tasks = await deps.chamber.list(TaskFilter())
    return len(tasks) == 2 and all(is_terminal(task.status) for task in tasks)


async def _tasks(deps: QueenDeps) -> dict[str, Task]:
    """The goal's tasks by title."""
    return {task.spec.title: task for task in await deps.chamber.list(TaskFilter())}


def _texts(message: Message) -> list[str]:
    """Every text a message carries, the brief included."""
    return [part.text for part in message.parts if isinstance(part, TextPart)]


# ──────────────────────────────────────────────────────────────────────────────
# Scenarios
# ──────────────────────────────────────────────────────────────────────────────


async def test_a_scout_reports_the_form_and_a_forager_logs_in_with_that_recon() -> None:
    briefs: list[str] = []

    deps = await _run_goal(_worker_turn(_scout_turn(feasible=True), _forager_turn(briefs)))

    tasks = await _tasks(deps)
    scout, login = tasks["Scout the login page"], tasks["Log in"]
    assert scout.status is TaskStatus.SUCCEEDED
    assert scout.outcome is not None and scout.outcome.scout_report is not None
    assert scout.outcome.scout_report.targets == (_FORM_TARGET,)
    # The Queen carried the report down: the Forager's brief shows the Scout's findings.
    brief = "\n".join(briefs)
    assert "<<<scout_findings>>>" in brief
    assert _FORM_FINDING in brief and _FORM_TARGET in brief
    # Its Warden verified both structural criteria on the attached browser.
    assert login.status is TaskStatus.SUCCEEDED
    recon = brief.split("<<<scout_findings>>>")[1].split("<<<end scout_findings>>>")[0]
    assert LOGIN_PASSWORD not in recon  # The Scout never saw, so never reported, the secret.
    # Every page the Forager read is Nectar: a Bee Bread entry at the task's clearance.
    entries = await BeeBread(deps.memory).by_task(login.id, HoneyClearance.C2)
    nectar = [entry for entry in entries if entry.kind is BeeBreadEntryKind.TOOL_RESULT]
    assert nectar and all(entry.clearance is HoneyClearance.C1 for entry in nectar)
    assert any(f"{_ORIGIN}/welcome" in (entry.payload or "") for entry in nectar)


async def test_an_infeasible_scout_holds_the_forager_back_and_the_goal_ends() -> None:
    briefs: list[str] = []

    deps = await _run_goal(_worker_turn(_scout_turn(feasible=False), _forager_turn(briefs)))

    tasks = await _tasks(deps)
    scout, login = tasks["Scout the login page"], tasks["Log in"]
    assert scout.status is TaskStatus.FAILED
    assert login.status is TaskStatus.CANCELLED  # Never dispatched, and the goal is over.
    assert login.outcome is not None
    assert "The login page is unusable." in login.outcome.summary
    assert briefs == []  # No Forager ever ran.
