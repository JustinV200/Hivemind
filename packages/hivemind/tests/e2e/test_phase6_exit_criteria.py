"""Prove phase 6's exit criteria for real: the fixture login, three placements, no leftovers.

`.claude/roadmap.md`'s phase 6 "Exit criteria" (after "## Phase 6") states three bullets; this
module proves each one against a real toolchain rather than only a fake one:

1. The fixture-site login goal succeeds in three placements: (a) a Linux Real Cell (the Hive
   Stand) with a lease-started Xvfb and openbox and a real Chromium; (b) a `desktop-ubuntu`
   Virtual Cell, following phase 5's own pattern (`ContainerSpawningFakeCellBackend` with the
   in-Cell Warden run in-process on this host's own peripherals); (c) the browser-only variant
   of (a), the one path the Windows Hive Stand also takes -- it needs no X11 toolchain at all, so
   it runs for real here on Linux too, and `docs/runbooks/phase6-windows-exit-check.md` is what an
   operator runs on an actual Windows machine to prove the rest.
2. After the Real Cell runs ((a) and (c)): no lease-started display, audio or browser process is
   still alive; the Hive Stand is left exactly as found (its tree unchanged outside the manifest's
   own data directory, its scratch empty); no screenshot bytes reach the captured logs or a trail
   event's payload.
3. The login's recording plays back with a before and an after frame for every action ((a)'s own
   recording, read straight from the Hive's database); a deliberately wrong click -- a real click
   whose declared `expect` can never hold -- fails its postcondition, is rolled back and raises an
   Alarm at once ((b)'s own scenario, checked on the trail's `capping.*` rows and the Alarm).

Every scenario scripts its planner reply and its Worker's tool calls with the same
`FakeLLMProvider` approach `tests.e2e.kernel_helpers`/`tests.builders.virtual_cells` already use
for the kernel and Virtual Cells e2e suites (`phase6_exit_helpers.py`, beside this file, holds
what they share). Marked `e2e`; skips with a clear reason when the X11/audio toolchain, Playwright
or a Chromium cannot be found here -- CI's own `e2e` job installs no extras, so every scenario
below skips there.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/roadmap.md phase 6 "Exit criteria" for the three bullets this module proves.
    - .claude/phase-6-handoff.md section 4.3 for this module's own plan.
    - tests.e2e.phase6_exit_helpers for the shared plan, scripts and assertions.
    - tests.e2e.test_kernel_on_hive_stand for the Hive Stand wiring this module's placement (a)
      and (c) scenarios reuse, scenario (h) most of all (the left-as-found snapshot).
    - tests.e2e.test_virtual_cells and tests.builders.virtual_cells for the
      ContainerSpawningFakeCellBackend wiring this module's placement (b) scenario reuses.
    - docs/runbooks/phase6-windows-exit-check.md for placement (c) on an actual Windows machine.
"""

from __future__ import annotations

import asyncio
import dataclasses
import functools
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from builders.cli import fake_manifest
from builders.virtual_cells import (
    ContainerSpawningFakeCellBackend,
    VirtualCellsTuning,
    virtual_cells_manifest,
)
from e2e.kernel_helpers import HaikuScript, pid_alive, snapshot_tree, wait_until
from e2e.phase6_exit_helpers import (
    LOGIN_PASSWORD,
    assert_no_screenshot_bytes,
    assert_two_frames_per_action,
    browser_missing,
    grant_hive_stand_full_access,
    login_container_script,
    login_plan,
    login_worker_turn,
    payloads_text,
    read_recording,
    serve_login_site,
    x11_toolchain_missing,
)

from hivemind.cell import HoneyClearance
from hivemind.cli.compose import GoalReport, Hive, build_hive, run_goal, run_hive
from hivemind.common.logging import configure_logging
from hivemind.llm import LLMRequest, LLMResponse
from hivemind.manifest import load_manifest
from hivemind.pheromone import PheromoneEvent, TrailQuery
from waggle.clock import SystemClock

pytestmark = pytest.mark.e2e

_TIMEOUT_S = 30.0  # Generous: a real Xvfb, openbox and Chromium start and log in well under this.
_GOAL = "Log in to the fixture site and reach the welcome page."
_MIN_LOGIN_ACTIONS = 4  # navigate, fill username, fill password, click submit.


def _skip_unless_real_desktop() -> None:
    """Skip with a clear reason unless the X11/audio toolchain and Playwright are both here."""
    reason = x11_toolchain_missing() or browser_missing()
    if reason:
        pytest.skip(reason)


def _skip_unless_browser() -> None:
    """Skip with a clear reason unless Playwright (and so a browser) can run here."""
    reason = browser_missing()
    if reason:
        pytest.skip(reason)


@dataclasses.dataclass(frozen=True, slots=True)
class _RealCellRun:
    """What criterion 2 needs from one Real Cell login run, bundled (codingrules section 5.1)."""

    report: GoalReport
    started_pids: tuple[int, ...]
    cell_id: str
    events: tuple[PheromoneEvent, ...]


async def _run_login_on_hive_stand(hive: Hive) -> _RealCellRun:
    """Run the login goal to completion on the Hive Stand; capture what criterion 2 checks after.

    Every value criterion 2 needs is read here, inside the `run_hive` block, because the sub-bee's
    retirement (and so its Exoskeleton detach) happens as part of the task finishing, and the
    lease itself closes once this function's own `async with` exits.
    """
    async with run_hive(hive):
        report = await run_goal(hive, _GOAL, clearance=HoneyClearance.C1, timeout_s=_TIMEOUT_S)
        lease = hive.warden.lease
        assert lease is not None
        started_pids = lease.started_pids
        events = await hive.stores.trail.query(TrailQuery())
        cell_id = str(hive.warden_link.cell.id)
    return _RealCellRun(report=report, started_pids=started_pids, cell_id=cell_id, events=events)


def _pid_is_dead(pid: int) -> Callable[[], bool]:
    """Build a zero-argument `wait_until` condition for one pid, binding it by value."""
    return lambda: not pid_alive(pid)


async def _assert_every_pid_dies(pids: Sequence[int]) -> None:
    """Wait for every lease-started pid to exit, mirroring scenario (h)'s own left-as-found wait."""
    for pid in pids:
        await wait_until(_pid_is_dead(pid), timeout_s=10.0)


def _assert_left_as_found(
    tmp_path: Path, data_dir: Path, before: dict[str, int], run: _RealCellRun
) -> None:
    """Assert criterion 2: empty scratch, an unchanged tree outside it, every pid dead."""
    assert snapshot_tree(tmp_path / "scratch") == {}
    assert snapshot_tree(tmp_path, exclude=(data_dir,)) == before
    assert len(run.started_pids) >= 1, "the run should have started at least one real process"
    asyncio.run(_assert_every_pid_dies(run.started_pids))


def _assert_no_screenshots_anywhere(captured: str, run: _RealCellRun) -> None:
    """Assert criterion 2: no screenshot bytes reached the captured logs or a trail payload."""
    assert_no_screenshot_bytes(captured, "the captured logs")
    assert_no_screenshot_bytes(payloads_text(run.events), "a trail event payload")
    assert LOGIN_PASSWORD not in captured, "the password leaked into the captured logs"


# ──────────────────────────────────────────────────────────────────────────────
# (a) Linux Real Cell: the Hive Stand, a lease-started Xvfb + openbox, a real Chromium.
# ──────────────────────────────────────────────────────────────────────────────


def test_real_cell_login_leaves_the_stand_as_found_and_records_both_frames(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """(1a) + (2) + (3, recording half): the login goal on the Hive Stand, for real.

    Through the real Queen, Warden, Capping gate, flight recorder and Drone. After the goal:
    every lease-started peripheral process is dead, the Hive Stand's own tree is exactly as
    found, its scratch is empty, and neither the captured logs nor a trail payload ever carries
    a screenshot's bytes. The recording plays back with a before and an after frame per action.
    """
    _skip_unless_real_desktop()
    configure_logging(json_output=True, level="DEBUG")

    manifest_path = fake_manifest(tmp_path)
    grant_hive_stand_full_access(manifest_path)
    data_dir = tmp_path / "data"

    with serve_login_site() as origin:
        script = HaikuScript(login_worker_turn(origin), plan=login_plan(origin))
        manifest = load_manifest(manifest_path, {})
        hive = build_hive(
            manifest, environ={}, clock=SystemClock(), responders={"fake": script.responder}
        )
        before = snapshot_tree(tmp_path, exclude=(data_dir,))
        run = asyncio.run(_run_login_on_hive_stand(hive))

    assert run.report.succeeded, run.report
    _assert_left_as_found(tmp_path, data_dir, before, run)

    captured = capsys.readouterr()
    _assert_no_screenshots_anywhere(captured.out + captured.err, run)

    info, actions = read_recording(data_dir / "hive.sqlite3", run.cell_id)
    assert len(actions) >= _MIN_LOGIN_ACTIONS
    assert_two_frames_per_action(info, actions)


# ──────────────────────────────────────────────────────────────────────────────
# (c) The browser-only variant of (a): what the Windows Hive Stand also runs.
# ──────────────────────────────────────────────────────────────────────────────


def test_browser_only_login_needs_no_x11_and_leaves_the_stand_as_found(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """(1c) + (2): `TaskNeeds.browser_only` on the Hive Stand -- no Xvfb, openbox or PulseAudio.

    `plan_attach` never starts a display for a browser-only need (`DisplaySource.NONE`), so this
    is the one Exoskeleton path the Windows Hive Stand also takes, Edge or Chrome found by the
    same browser locator in place of a pre-installed Chromium. Runs for real here on Linux too,
    proving the shared code path; `docs/runbooks/phase6-windows-exit-check.md` is what an
    operator runs on an actual Windows machine to prove the rest.
    """
    _skip_unless_browser()
    configure_logging(json_output=True, level="DEBUG")

    manifest_path = fake_manifest(tmp_path)
    grant_hive_stand_full_access(manifest_path)
    data_dir = tmp_path / "data"

    with serve_login_site() as origin:
        script = HaikuScript(login_worker_turn(origin), plan=login_plan(origin, browser_only=True))
        manifest = load_manifest(manifest_path, {})
        hive = build_hive(
            manifest, environ={}, clock=SystemClock(), responders={"fake": script.responder}
        )
        before = snapshot_tree(tmp_path, exclude=(data_dir,))
        run = asyncio.run(_run_login_on_hive_stand(hive))

    assert run.report.succeeded, run.report
    _assert_left_as_found(tmp_path, data_dir, before, run)

    captured = capsys.readouterr()
    _assert_no_screenshots_anywhere(captured.out + captured.err, run)


# ──────────────────────────────────────────────────────────────────────────────
# (b) A desktop-ubuntu Virtual Cell: phase 5's own container-spawning pattern.
# ──────────────────────────────────────────────────────────────────────────────


def _fake_backend(hive: Hive) -> ContainerSpawningFakeCellBackend:
    """Return the running Hive's own container-spawning fake backend."""
    assert hive.virtual_cells is not None
    backend = hive.virtual_cells.registry.get("fake")
    assert isinstance(backend, ContainerSpawningFakeCellBackend)
    return backend


async def _run_login_on_a_virtual_cell(
    hive: Hive,
) -> tuple[GoalReport, str, tuple[PheromoneEvent, ...]]:
    """Run the login goal to completion on whichever Virtual Cell placement chose."""
    backend = _fake_backend(hive)
    stand_cell_id = str(hive.warden_link.cell.id)
    try:
        async with run_hive(hive):
            report = await run_goal(hive, _GOAL, clearance=HoneyClearance.C1, timeout_s=_TIMEOUT_S)
            events = await hive.stores.trail.query(TrailQuery())
    finally:
        await backend.aclose()
    assigned = {e.payload["cell_id"] for e in events if e.kind == "queen.assigned"}
    # The exit criterion names a Virtual Cell: the Hive Stand itself must have run nothing.
    assert stand_cell_id not in assigned, (
        "the login goal landed on the Hive Stand, not a Virtual Cell"
    )
    cell_id = next(iter(assigned))
    return report, str(cell_id), events


def _worker_turn_never_called(request: LLMRequest) -> LLMResponse:
    """Fail loudly if the Hive Stand's own provider ever sees a WORKER-slot call here.

    In a `prefer = "virtual"` placement the Drone runs inside the Virtual Cell, against that
    Cell's own `FakeLLMProvider` (`login_container_script`, seeded through `_build_backend_
    factory`'s own `script=`); the Hive Stand's provider only ever plans the goal.
    """
    raise AssertionError(f"unexpected WORKER-slot call on the Hive Stand's own provider: {request}")


def _build_backend_factory(origin: str) -> functools.partial[ContainerSpawningFakeCellBackend]:
    """Build a `FakeCellBackend`-shaped factory whose containers run the wrong-click login script.

    `ContainerSpawningFakeCellBackend.__init__`'s own `script` parameter is fixed at
    construction time by the composition root (`hivemind.cli.compose.virtual_cell_backends.
    build_registry`), which calls it as `FakeCellBackend(clock, endpoint=..., gate=...)`; binding
    `script` here with `functools.partial` (rather than patching the class directly, as
    `tests.e2e.test_virtual_cells` does for its own unscripted, default-script scenarios) lets
    that same call still fill in `clock`/`endpoint`/`gate` while this test's own script rides
    along, and the returned object is still a plain `ContainerSpawningFakeCellBackend` instance.
    """
    script = functools.partial(login_container_script, origin, wrong_click=True)
    return functools.partial(ContainerSpawningFakeCellBackend, script=script)


def test_virtual_cell_login_rolls_back_a_wrong_click_and_raises_an_alarm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(1b) + (3, wrong-click half): the same login on a `desktop-ubuntu` Virtual Cell.

    `ContainerSpawningFakeCellBackend` (phase 5's own pattern) fakes only the container
    infrastructure; the in-Cell Warden it runs in-process drives this host's own real Xvfb,
    openbox and Chromium. After the correct login, a deliberately wrong click -- a real click on
    the welcome page's own heading, whose declared `expect` names a URL it can never produce --
    fails its postcondition, is rolled back (`RollbackMethod.GUI_STATE`) and raises a
    `POSTCONDITION_FAILED` Alarm at once, without disturbing the page the task's own acceptance
    still checks afterwards.
    """
    _skip_unless_real_desktop()

    manifest_path = virtual_cells_manifest(
        tmp_path, tuning=VirtualCellsTuning(prefer="virtual", overwinter_enabled=False)
    )

    with serve_login_site() as origin:
        monkeypatch.setattr(
            "hivemind.cli.compose.virtual_cell_backends.FakeCellBackend",
            _build_backend_factory(origin),
        )
        # The Hive Stand's own QUEEN-slot responder plans the goal; see _worker_turn_never_called.
        script = HaikuScript(_worker_turn_never_called, plan=login_plan(origin))
        manifest = load_manifest(manifest_path, {})
        hive = build_hive(
            manifest, environ={}, clock=SystemClock(), responders={"fake": script.responder}
        )
        report, _cell_id, events = asyncio.run(_run_login_on_a_virtual_cell(hive))

    assert report.succeeded, report

    kinds = [event.kind for event in events]
    assert "capping.rolled_back" in kinds
    rolled_back = next(e for e in events if e.kind == "capping.rolled_back")
    assert rolled_back.payload == {"method": "GUI_STATE"}
    alarms = [
        e
        for e in events
        if e.kind == "alarm.raised" and e.payload.get("kind") == "POSTCONDITION_FAILED"
    ]
    assert alarms, f"expected a POSTCONDITION_FAILED alarm.raised event; trail kinds: {kinds}"
