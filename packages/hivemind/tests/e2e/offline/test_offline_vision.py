"""End-to-end: the Forager logs in with an accessibility tree or with screenshots, by capability.

Roadmap step 8.7 (local vision). A local model usually cannot see; the Forager (the browsing
Worker) must then work from the page's accessibility tree, and send a screenshot only to a binding
that declares `vision`. The phase 6 browser-only login goal runs here on the Hive Stand with a real
Chromium and the scripted loopback server as WORKER, twice, differing in one manifest field,
`[llm.providers.local.capabilities] vision`. With `vision = false` (what `offline.toml` declares):
the goal succeeds, `browser_snapshot` is offered and used, `browser_screenshot` and `see` are never
offered or called, and no request the server received carried an image part. With `vision = true`
the screenshot tool is offered, the script calls it, and the next request carries the image: it is
the capability that switches the path. The server records only the shape of each request (tool
names, one boolean for an image part), never a body. The real-model counterpart is `local_llm`-gated
in `tests/evals/offline/`.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - Both runs use native tool calls and a roomy context window, so the only difference between
      them is `vision`.
    - Skips exactly where `phase6_exit_helpers` does: no Playwright.

See Also:
    - tests/e2e/test_phase6_exit_criteria.py for the browser-only login this reuses.
    - hivemind.workers.tools.exoskeleton.offer for where vision decides the tools offered.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from e2e.offline import (
    EgressLog,
    assert_every_call_local,
    calls_on,
    offline_manifest,
    scripted_model_ids,
)
from e2e.offline.rig import TIMEOUT_S
from e2e.phase6_exit_helpers import (
    browser_missing,
    login_plan,
    login_tool_calls,
    serve_login_site,
)
from e2e.scripted_openai import RequestShape, Scenario, Step, serve_in_thread

from hivemind.cell import HoneyClearance
from hivemind.cli.compose import Hive, build_hive, run_goal, run_hive
from hivemind.forage.slots import ModelSlot
from hivemind.memory import BeeBreadEntryKind
from hivemind.pheromone import MAX_QUERY_LIMIT, PheromoneEvent, TrailQuery
from waggle.clock import SystemClock

pytestmark = pytest.mark.e2e

_GOAL = "Log in to the fixture site and reach the welcome page."
_SNAPSHOT, _SCREENSHOT, _SEE = "browser_snapshot", "browser_screenshot", "see"
# Native tool calls so the request names its tools exactly; a window the Exoskeleton's tool list
# fits in; and the Hive Stand at FULL access, as the phase 6 browser-only test patches it.
_CAPABILITIES = {"native_tool_calls": True, "context_window": 32768}
_FULL_ACCESS = {"hive_stand": {"access_level": "FULL"}}


@pytest.mark.parametrize("vision", [False, True])
def test_the_forager_uses_the_accessibility_tree_unless_the_binding_can_see(
    tmp_path: Path, egress: EgressLog, vision: bool
) -> None:
    if reason := browser_missing():
        pytest.skip(reason)
    with serve_login_site() as origin:
        scenario = Scenario(plan_json=login_plan(origin, browser_only=True), steps=_steps(origin))
        with serve_in_thread(scenario) as base_url:
            manifest, _path = offline_manifest(
                tmp_path, base_url, _FULL_ACCESS, {**_CAPABILITIES, "vision": vision}
            )
            hive = build_hive(manifest, environ={}, clock=SystemClock())

            succeeded, events, page_reads = asyncio.run(_log_in(hive))

    assert succeeded
    assert page_reads >= 1  # The accessibility tree was read and kept as Nectar.
    assert_every_call_local(events, manifest)
    assert calls_on(events, ModelSlot.WORKER)
    shapes = [s for s in scenario.switches.shapes if s.model == scripted_model_ids()["worker"]]
    offered = {tool for shape in shapes for tool in shape.tools}
    issued = {call for shape in shapes for call in shape.issued}
    assert _SNAPSHOT in offered and _SNAPSHOT in issued  # The tree path is there either way.
    _assert_the_path_the_capability_picks(vision, shapes, offered, issued)
    egress.assert_clean_and_live()


def _assert_the_path_the_capability_picks(
    vision: bool, shapes: list[RequestShape], offered: set[str], issued: set[str]
) -> None:
    """Without vision no pixel tool is offered, called or sent; with it, all three happen."""
    sent_an_image = any(shape.has_image_part for shape in shapes)
    if vision:
        assert _SCREENSHOT in offered and _SCREENSHOT in issued
        assert sent_an_image  # The screenshot came back to the model as an image part.
        return
    assert not {_SCREENSHOT, _SEE} & offered
    assert not {_SCREENSHOT, _SEE} & issued
    assert not sent_an_image


def _steps(origin: str) -> tuple[Step, ...]:
    """The login as a model would drive it: open the page, read its tree, look if it can, act."""
    opening, *acting = login_tool_calls(origin)
    steps = [Step(call[1], call[2]) for call in (opening, *acting)]
    return (
        steps[0],
        Step(_SNAPSHOT),
        Step(_SCREENSHOT, only_if_offered=True),  # Only a binding that can see is offered it.
        *steps[1:],
    )


async def _log_in(hive: Hive) -> tuple[bool, tuple[PheromoneEvent, ...], int]:
    """Run the login goal; return success, the trail and how many page reads were kept."""
    async with run_hive(hive):
        report = await run_goal(hive, _GOAL, clearance=HoneyClearance.C1, timeout_s=TIMEOUT_S)
        events = await hive.stores.trail.query(TrailQuery(limit=MAX_QUERY_LIMIT))
        kept = await hive.stores.memory.list_bee_bread_by_task(
            report.tasks[0].id, HoneyClearance.C2
        )
    reads = sum(1 for entry in kept if entry.kind is BeeBreadEntryKind.TOOL_RESULT)
    return report.succeeded, tuple(events), reads
