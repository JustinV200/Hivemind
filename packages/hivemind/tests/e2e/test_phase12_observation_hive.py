"""End-to-end: roadmap phase 12's exit criteria, in a real browser against a real Hive.

`.claude/roadmap.md` phase 12 exit criteria are about what a person sees and does in the Observation
Hive (the React app the Hive Entrance, the Hive's one HTTP door, serves), so this test is that
person. ``hive serve``'s own composition (a real Queen, her Warden supervising the Hive Stand, the
machine she runs on, and a Drone, the worker bee, over the stand's scripted provider whose goal asks
the human one question) serves the built app from ``packages/observation-web/dist`` on its loopback
listener, and a headless Chromium (Playwright, a virtual authenticator standing in for the person's
passkey prompt) joins by invite link, waits to be approved at the Hive Stand, logs in with passkey
plus password, and watches and talks: the summary strip, the fleet, the Cell page, the Queen's
thoughts and Attendant, the chat (the question comes back there and is answered there), and the
Honey browser. ``e2e.observation_browser`` is the driver plumbing; the page's script is
``packages/observation-web/e2e``. The browser's device holds what a phone is granted by default
(its Honey reach narrowed to ``/hive``, so the Cell-folder 403 stays provable), which includes the
work a goal needs, and the goal is requested from the chatbox itself: the composer's "Request as a
goal" switch posts it to ``POST /v1/goals``. A second, enrolled program is only an instrument: it
reads ``/v1/summary`` beside the strip and wakes the Queen with a message while the browser is on
another view.

Proved here, one assertion per exit-criteria bullet: the fleet and the Cell page show each Cell's
Comb Shield tier and Pheromone Mask badge; the strip's counters are numbers and the active-bee
count moved with no reload and matches ``/v1/summary``; a task, requested from the chatbox, ran end
to end through the chat (its question, the answer, the Queen's reply); the Queen's thoughts gained
episodes and her Attendant graph moved with no reload (a second tab of the device watches it, since
a goal is planned within a second of being asked for); the Honey browser shows ``/hive`` and
answers a Cell folder with 403 to a device holding ``observe:honey:hive`` only; the page made no
write but the chat's, the inbox's, the goals' and the Entrance's own; no page errors; at 360 px the
strip, the fleet and the chat render.

Not provable here, with what would prove them: a phone over the VPN, the PWA install and the strip
and chat working on that phone (a phone on the overlay network and Chrome's install prompt);
the Android APK enrolling by QR, push received in the background, a share from another app (the
phase 12.12 APK on a device or emulator, with FCM or UnifiedPush); Cells appearing and grants
flowing as a phase 6 or 9 scenario runs (those scenarios need Nucs and Virtual Cell backends this
container lacks; here the Hive Stand is the one Cell); the trail scrolling (the app has no trail
view); the loopback-only screens absent on the remote listener (needs a browser on the overlay
name, covered by the Python route test).

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - The page is never reloaded after the invite opens: one document load, one marker.
    - Skipped, with the reason, where Node, Playwright, Chromium or the app's build is missing.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from builders.entrance.landing import LandingClient, LandingSession
from e2e.entrance_stand import ANSWER, CHAT, GOAL, QUESTION, REPLY, Stand, build_served, standing
from e2e.observation_browser import DIST, Browser, Step, launched, unavailable

from hivemind.cli.compose.entrance import OBSERVATION_BUILD, ServedHive
from hivemind.guard import load_guard_policy, proposed_set
from hivemind.manifest import HiveManifest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(unavailable() is not None, reason=unavailable() or "available"),
]

# What the operator grants the browser device: the set a phone gets when the operator names
# nothing (the work its goals do included), with Honey narrowed from every scope to ``/hive`` so the
# Cell-folder refusal below stays a thing the run can prove.
CAPABILITIES = (
    *(c for c in proposed_set(load_guard_policy()).as_strings() if c != "observe:honey:*"),
    "observe:honey:hive",
)
TIERS = ("MEADOW", "PROPOLIS", "NIGHT VEIL")
MASKS = ("OFF", "WARDEN", "QUEEN FORCED")
# The only writes the page may make, by path prefix.
WRITES = (
    "/v1/auth/",
    "/v1/enrol/passkey",
    "/v1/chat",
    "/v1/inbox/",
    "/v1/goals",
    "/v1/push/subscriptions",
    "/v1/entrance/",
    "/v1/devices/",
)
PHONE = (360, 740)
_SETTLE_TRIES = 6
_SETTLE_S = 0.5
OUT_ENV = "HIVE_E2E_OUT"


@dataclass(frozen=True, slots=True)
class _Run:
    """What the browser saw, by step, and what the Entrance's own summary said at the end."""

    steps: dict[str, Step]
    summary: dict[str, Any]


def test_the_observation_hive_meets_its_exit_criteria_in_a_real_browser(tmp_path: Path) -> None:
    # Screenshots and the report stay after the run when HIVE_E2E_OUT names a directory.
    out_dir = Path(os.environ.get(OUT_ENV) or tmp_path / "observation")
    out_dir.mkdir(parents=True, exist_ok=True)
    served_build = tmp_path / OBSERVATION_BUILD
    served_build.parent.mkdir(parents=True)
    # `hive serve` finds the build manifest-relative; link the real one in.
    served_build.symlink_to(DIST, target_is_directory=True)
    manifest, served = build_served(tmp_path)

    run = asyncio.run(_scenario(manifest, served, out_dir))

    _assert_exit_criteria(run)


async def _scenario(manifest: HiveManifest, served: ServedHive, out_dir: Path) -> _Run:
    """Serve the Hive, drive the browser through the whole run, and return its key answers."""
    async with standing(manifest, served) as stand:
        base = f"http://localhost:{stand.entrance.listeners.loopback_port}"
        async with launched(base, out_dir, stand.password) as browser:
            seen = await _drive(stand, browser, base)
            await browser.end()
    return seen


async def _program(stand: Stand) -> tuple[LandingClient, LandingSession]:
    """Enrol and approve a program: it reads the summary and wakes the Queen; it submits no goal."""
    client = LandingClient(
        stand.console.http, stand.served.hive.manifest.hive.id, stand.served.hive.clock
    )
    key = await client.enrol(await stand.invite("garden-bot"))
    await stand.approve(key.device_id, "garden-bot")
    return client, await client.login(key, stand.password)


async def _drive(stand: Stand, browser: Browser, base: str) -> _Run:
    """Join, log in, then watch the Hive run a goal and talk to it; keep each key step."""
    seen: dict[str, Step] = {}
    invite = await stand.mint("observer")
    link = urlsplit(str(invite["url"]))
    await browser.step("join", inviteUrl=f"{base}{link.path}#{link.fragment}")
    await stand.approve(str(invite["device_id"]), "observer", CAPABILITIES)
    seen["login"] = await browser.step("await_approval_and_login")
    seen["strip"] = await browser.step("read_strip")
    seen["fleet"] = await browser.step("fleet_rows")
    # A quiet Hive must settle to live with a current strip once the hello deadline has passed.
    seen["live"] = await browser.step("await_live")
    seen["cell"] = await browser.step("cell_page")
    client, program = await _program(stand)

    # A second tab of the device watches the Queen's Attendant while the goal is asked for in the
    # first tab's chatbox, which then follows the goal's line there. The goal is planned and placed
    # within a second, so only a tab already on the Attendant can see its graph move on its own.
    seen["attendant_before"] = await browser.step("watch_attendant")
    seen["goal"] = await browser.step("request_goal", text=GOAL)
    seen["attendant_moved"] = await browser.step("watch_attendant_wait")
    await stand.until(stand.question_waiting)
    seen["qa"] = await browser.step("await_question_and_answer", answer=ANSWER)
    await stand.until(lambda: stand.goal_finished(str(seen["goal"].result["requestId"])))
    seen["attendant_done"] = await browser.step("attendant")

    # The Queen's thoughts are open and following while she is woken by a message.
    seen["thoughts_before"] = await browser.step("thoughts")
    await client.call(program, "POST", "/v1/chat", {"text": "status?"})
    seen["thoughts_after"] = await browser.step("thoughts_wait", timeoutMs=12000)
    seen["sent"] = await browser.step("send_chat", text=CHAT)
    seen["reply"] = await browser.step("await_reply", text=REPLY, minCount=2)
    seen["honey"] = await _honey(browser, seen["fleet"])
    seen["strip_end"], summary = await _strip_beside_summary(browser, client, program)
    seen["trail"] = await browser.step("trail_scrolls")

    # A second, short pass at phone width on the same page and session.
    await browser.step("viewport", width=PHONE[0], height=PHONE[1])
    seen["phone_strip"] = await browser.step("read_strip")
    seen["phone_fleet"] = await browser.step("fleet_rows")
    seen["phone_chat"] = await browser.step("chat_layout")
    seen["errors"] = await browser.step("console_errors")
    return _Run(seen, summary)


async def _strip_beside_summary(
    browser: Browser, client: LandingClient, program: LandingSession
) -> tuple[Step, dict[str, Any]]:
    """Read the strip and the Entrance's own summary together, retrying while a count settles."""
    for _ in range(_SETTLE_TRIES):
        summary = (await client.call(program, "GET", "/v1/summary")).json()
        strip = await browser.step("read_strip")
        if strip.result["bees"] == summary["active_bees"] and strip.result["cells"] == [
            summary["cells_active"],
            summary["cells_inactive"],
        ]:
            break
        # The counts are a sliding window: give a live update a moment to land, then ask again.
        await asyncio.sleep(_SETTLE_S)
    return strip, summary


async def _honey(browser: Browser, fleet: Step) -> Step:
    """Open the Honey top, then a folder the device may read and a Cell's, which it may not."""
    [cell] = [row["id"] for row in fleet.result["rows"]]
    return await browser.step(
        "honey", folders=["#/honey/hive"], forbidden=[f"#/honey/cells/{cell}"]
    )


def _assert_exit_criteria(run: _Run) -> None:
    """One check per exit-criteria bullet the container can prove."""
    checks = (
        _assert_badges,
        _assert_strip,
        _assert_strip_matches_summary,
        _assert_task_through_the_chat,
        _assert_attendant_moved,
        _assert_thoughts_moved,
        _assert_never_reloaded,
        _assert_honey,
        _assert_only_allowed_writes,
        _assert_no_page_errors,
        _assert_phone_width,
        _assert_layout_and_connection,
    )
    failed: list[str] = []
    for check in checks:
        try:
            check(run)
        except AssertionError as error:
            failed.append(f"{check.__name__}: {error}")
    # Every bullet is checked even when one fails, so one run reports all that are broken.
    assert not failed, "\n".join(failed)


def _assert_badges(run: _Run) -> None:
    """Each Cell's tier and mask are badges in the fleet and in the Cell page header."""
    seen = run.steps
    rows = seen["fleet"].result["rows"]
    [row] = rows
    assert row["tier"] in TIERS and row["mask"] in MASKS, row
    assert (seen["cell"].result["tier"], seen["cell"].result["mask"]) == (row["tier"], row["mask"])
    assert seen["fleet"].result["legend"]


def _assert_strip(run: _Run) -> None:
    """The strip's counters are numbers, and the active bees moved without a reload."""
    seen = run.steps
    final = seen["strip_end"].result
    for key in ("cells", "kind", "thinking"):
        assert len(final[key]) == 2 and all(isinstance(n, int) for n in final[key]), final
    assert isinstance(final["bees"], int)
    # The active-bee count took at least two values while the goal ran, and the page never reloaded.
    assert len(set(final["beeHistory"]) - {None}) >= 2, final["beeHistory"]


def _assert_strip_matches_summary(run: _Run) -> None:
    """The strip shows what the Entrance's own summary counts at the same moment."""
    final, truth = run.steps["strip_end"].result, run.summary
    shown = {
        "bees": final["bees"],
        "cells": final["cells"],
        "kind": final["kind"],
        "thinking": final["thinking"],
    }
    counted = {
        "bees": truth["active_bees"],
        "cells": [truth["cells_active"], truth["cells_inactive"]],
        "kind": [truth["cells_real"], truth["cells_virtual"]],
        "thinking": [truth["bees_awake"], truth["bees_autopilot"]],
    }
    differ = {key: (shown[key], counted[key]) for key in shown if shown[key] != counted[key]}
    assert not differ, f"strip versus /v1/summary, per counter: {differ}"


def _assert_task_through_the_chat(run: _Run) -> None:
    """The goal came from the chatbox, its question came back there, and the Queen replied."""
    seen = run.steps
    goal = seen["goal"].result
    assert goal["offByDefault"] and goal["text"] == GOAL and goal["boxAfter"] == "", goal
    assert goal["href"] == "#/attendant/queen" and goal["requestId"], goal
    assert seen["qa"].result["asked"] == QUESTION and seen["qa"].result["resolved"]
    [node] = seen["attendant_done"].result["nodes"]
    assert node["status"] == "succeeded", node
    assert seen["sent"].result["text"] == CHAT
    assert REPLY in seen["reply"].result["replies"]


def _assert_attendant_moved(run: _Run) -> None:
    """The Attendant graph gained its node with the watching tab's document untouched."""
    seen = run.steps
    graph, before = seen["attendant_moved"].result, seen["attendant_before"].result
    assert before["nodes"] == [] and graph["changed"] and graph["nodes"], (before, graph)
    assert before["loads"] == 1 and graph["loadsDuring"] == 0, (before, graph)
    assert graph["loadsDuring"] == 0, graph


def _assert_thoughts_moved(run: _Run) -> None:
    """The Queen's thoughts gained episodes with follow on and the document untouched."""
    seen = run.steps
    thoughts = seen["thoughts_after"].result
    assert thoughts["grew"] and thoughts["after"] > thoughts["before"], thoughts
    assert thoughts["loadsDuring"] == 0, thoughts


def _assert_never_reloaded(run: _Run) -> None:
    """One document load and one marker across every step: nothing ever reloaded the page."""
    seen = run.steps
    loads = {step.loads for step in seen.values()}
    markers = {step.marker for step in seen.values()}
    assert loads == {1}, f"the page was loaded again: {loads}"
    assert len(markers) == 1 and None not in markers, "the document was replaced"


def _assert_honey(run: _Run) -> None:
    """The device sees /hive; a Cell folder answers 403, and the page asked for nothing else."""
    seen = run.steps
    honey = seen["honey"].result
    assert "hive" in [folder["name"] for folder in honey["top"]["folders"]], honey["top"]
    [(_, refused)] = honey["forbidden"].items()
    assert refused["alert"] and "403" in " ".join(refused["statuses"]), refused


def _assert_only_allowed_writes(run: _Run) -> None:
    """No request the page made was a write outside the chat and the Entrance's own routes."""
    seen = run.steps
    writes = {(m, p) for step in seen.values() for m, p in step.requests if m != "GET"}
    stray = {(m, p) for m, p in writes if not p.startswith(WRITES)}
    assert writes and not stray, stray


def _assert_no_page_errors(run: _Run) -> None:
    """No uncaught page error, and no console error but the Honey folder's expected 403."""
    seen = run.steps
    errors: dict[str, Any] = seen["errors"].result
    assert errors["pageErrors"] == [], errors["pageErrors"]
    assert [e for e in errors["consoleErrors"] if "403" not in e] == [], errors["consoleErrors"]


def _assert_phone_width(run: _Run) -> None:
    """At 360 px the strip, the fleet and the chat render, with no sideways scroll."""
    seen = run.steps
    assert seen["phone_strip"].result["bees"] is not None
    [row] = seen["phone_fleet"].result["rows"]
    assert row["tier"] in TIERS and row["mask"] in MASKS
    assert seen["phone_chat"].result["composer"] and seen["phone_chat"].result["send"]
    assert [seen[k].overflow_x for k in ("phone_strip", "phone_fleet", "phone_chat")] == [False] * 3


def _assert_layout_and_connection(run: _Run) -> None:
    """The fleet is a table at 1280 px and cards at 360 px; a quiet Hive reads live, not stale."""
    seen = run.steps
    # Header and row are both table rows on the desktop; at phone width the row is a flex card.
    assert seen["fleet"].result["layout"] == {
        "head": "table-row",
        "row": "table-row",
        "cell": "table-cell",
    }, seen["fleet"].result["layout"]
    phone = seen["phone_fleet"].result["layout"]
    assert phone["row"] == "flex" and phone["cell"] != "table-cell", phone
    assert seen["live"].result == {"word": "live", "stale": None}
