"""Define attach: equip one Cell with exactly the Exoskeleton one task needs, through its session.

`attach` (roadmap step 6.4, ADR-0031) is the one way a Cell gets a display, input, audio or a
browser. It plans from capabilities (`attach.plan`), then starts only what the plan names, each
through the Cell's own `CellSession` so the lease knows every process: a display (the operator's
own when allowed, else a private Xvfb and window manager, `attach.display`), a sound server
(`attach.audio`), and a browser (the injected `BrowserLauncher`, headed in the display when there
is one). The peripherals are then built over what started, and `cell.exoskeleton_attached` goes on
the trail. If any step fails, everything already started is stopped before the error leaves, so a
failed attach leaves the Cell as it found it; a successful one is undone by
`ExoskeletonHandle.detach`, and the lease's own release is the backstop for both.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.exoskeleton.attach`.
    Called by the Warden's spawn path for a task whose needs carry an Exoskeleton. Calls into
    `hivemind.cell`, `hivemind.guard` (CapabilitySet), `hivemind.pheromone` (PheromoneTrail), the
    peripheral backends and the other `attach` modules; waggle for the clock and the need.

Key invariants:
    - Nothing starts before the plan is known to be satisfiable.
    - On any failure every process this call started is stopped before the error propagates.
    - A browser-only need never gets a display, and never runs anything but the browser.

See Also:
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md for the whole design.
    - hivemind.exoskeleton.attach.handle for detach.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from pydantic import JsonValue

from hivemind.cell import BackgroundProcess, Cell, CellIdentity, CellSession, ExecSpec, run
from hivemind.exoskeleton.antennae.xdotool import XdotoolAntennae
from hivemind.exoskeleton.attach.audio import start_sound_server
from hivemind.exoskeleton.attach.display import (
    StartedDisplay,
    borrow_running_display,
    start_lease_display,
)
from hivemind.exoskeleton.attach.handle import ExoskeletonHandle, ExoskeletonTrail, Peripherals
from hivemind.exoskeleton.attach.plan import AttachPlan, DisplaySource, plan_attach
from hivemind.exoskeleton.attach.ready import Deadline, stop_all
from hivemind.exoskeleton.browser.base import Browser, BrowserLaunch, BrowserLauncher
from hivemind.exoskeleton.buzz.pulseaudio import PulseAudioBuzz, PulseServer
from hivemind.exoskeleton.compound_eye.x11 import X11CompoundEye
from hivemind.exoskeleton.errors import AttachError
from hivemind.exoskeleton.geometry import ScreenSize
from hivemind.exoskeleton.scratch import ScratchLayout
from hivemind.guard import CapabilitySet
from hivemind.pheromone import PheromoneTrail
from waggle.clock import Clock
from waggle.messages.task import ExoskeletonNeed

DEFAULT_SCREEN = ScreenSize(1280, 800)  # A laptop-sized desktop: every site lays out for it.
DEFAULT_READY_TIMEOUT_S = 30.0  # Display, sound server and browser together, on a slow Cell.
_MKDIR_TIMEOUT_S = 10.0  # Creating a few directories in scratch.

__all__ = [
    "DEFAULT_READY_TIMEOUT_S",
    "DEFAULT_SCREEN",
    "AttachDeps",
    "ExoskeletonConfig",
    "attach",
]


@dataclass(frozen=True, slots=True)
class ExoskeletonConfig:
    """How attach equips a Cell, chosen by the composition root.

    Attributes:
        screen: The size of a display attach starts.
        ready_timeout_s: The budget for everything attach starts to become usable.
        browser_sandbox: Whether the browser keeps Chromium's own sandbox. The composition root
            sets False only where the Cell is itself the sandbox (a Virtual Cell, whose
            container drops the capabilities the sandbox needs) or the Hive runs as root.
    """

    screen: ScreenSize = DEFAULT_SCREEN
    ready_timeout_s: float = DEFAULT_READY_TIMEOUT_S
    browser_sandbox: bool = True


@dataclass(frozen=True, slots=True)
class AttachDeps:
    """What attach needs beyond the Cell, its session, the need and the grant."""

    trail: PheromoneTrail
    identity: CellIdentity  # Who the trail events are recorded as.
    clock: Clock
    config: ExoskeletonConfig = field(default_factory=ExoskeletonConfig)
    browser_launcher: BrowserLauncher | None = None  # None: this Hive cannot start a browser.


@dataclass
class _Attaching:
    """One attach in progress: where it works, its budget, and every process it has started."""

    session: CellSession
    layout: ScratchLayout
    deadline: Deadline
    deps: AttachDeps
    started: list[BackgroundProcess] = field(default_factory=list)
    environment: dict[str, str] = field(default_factory=dict)


async def attach(
    cell: Cell,
    session: CellSession,
    need: ExoskeletonNeed,
    granted: CapabilitySet,
    deps: AttachDeps,
) -> ExoskeletonHandle:
    """Equip `cell` for one task, through `session`, and return the handle that detaches it.

    Args:
        cell: The Cell, for its capability report and id.
        session: The task's session on it; every process starts through it.
        need: What the task asked for.
        granted: The capabilities the task's bee holds.
        deps: The trail, identity, clock, config and browser launcher.

    Returns:
        The handle: plan, peripherals, environment, and `detach`.

    Raises:
        AttachError: The need cannot be met here, or something attach started never became
            ready. Nothing attach started is left running.
    """
    plan = _fit_launcher(plan_attach(need, cell.capabilities, granted), need, deps)
    layout = ScratchLayout.under(session.scratch_dir)
    deadline = Deadline.after(deps.clock, deps.config.ready_timeout_s)
    work = _Attaching(session, layout, deadline, deps, environment=layout.home_environment())
    try:
        peripherals = await _equip(work, plan)
    except BaseException:
        # Whatever failed, cancellation included: leave the Cell as found, then propagate.
        await stop_all(session, work.started)
        raise
    trail = ExoskeletonTrail(deps.trail, deps.identity, deps.clock, cell.id)
    handle = ExoskeletonHandle(
        plan, peripherals, work.environment, session, tuple(work.started), trail
    )
    await trail.record("cell.exoskeleton_attached", _attached_payload(plan, work.started))
    return handle


def _fit_launcher(plan: AttachPlan, need: ExoskeletonNeed, deps: AttachDeps) -> AttachPlan:
    """Drop the browser from a desktop plan when no launcher exists; refuse a browser-only one."""
    if not plan.browser or deps.browser_launcher is not None:
        return plan
    if need.browser_only:
        raise AttachError("this Hive has no browser launcher configured (the browser extra)")
    return replace(plan, browser=False)  # A desktop without the fast path still works.


async def _equip(work: _Attaching, plan: AttachPlan) -> Peripherals:
    """Start what `plan` names, in order, and build the peripherals over what started."""
    if plan.display is not DisplaySource.NONE or plan.audio:
        await _make_directories(work)
    shown = await _attach_display(work, plan)
    server = await _attach_sound(work) if plan.audio else None
    browser = await _attach_browser(work, headed=shown is not None) if plan.browser else None
    if shown is None:
        return Peripherals(buzz=_buzz(work, server), browser=browser)
    return Peripherals(
        compound_eye=X11CompoundEye(work.session, shown.display, work.deps.clock),
        antennae=XdotoolAntennae(work.session, shown.display),
        buzz=_buzz(work, server),
        browser=browser,
    )


async def _make_directories(work: _Attaching) -> None:
    """Create HOME, the runtime directory and each process's own directory, private, in scratch."""
    directories = tuple(str(path) for path in work.layout.directories())
    argv = ("mkdir", "-p", "-m", "700", *directories)
    made = await run(work.session, ExecSpec(argv=argv, timeout_s=_MKDIR_TIMEOUT_S))
    if made.exit_code != 0:
        raise AttachError("could not create the Exoskeleton's directories in scratch")


async def _attach_display(work: _Attaching, plan: AttachPlan) -> StartedDisplay | None:
    """Start or borrow the display the plan names, or none for a browser-only task."""
    if plan.display is DisplaySource.NONE:
        return None
    if plan.display is DisplaySource.RUNNING:
        shown = await borrow_running_display(work.session)
    else:
        screen = work.deps.config.screen
        shown = await start_lease_display(work.session, work.layout, screen, work.deadline)
    work.started.extend(shown.processes)
    work.environment.update(shown.display.environment())
    return shown


async def _attach_sound(work: _Attaching) -> PulseServer:
    """Start the lease's sound server and point every later process at it."""
    sound = await start_sound_server(work.session, work.layout, work.deadline)
    work.started.extend(sound.processes)
    work.environment.update(sound.server.environment())
    return sound.server


async def _attach_browser(work: _Attaching, *, headed: bool) -> Browser:
    """Launch the lease's browser: headed in the display when there is one, headless otherwise."""
    launcher = work.deps.browser_launcher
    if launcher is None:
        raise AttachError("no browser launcher is configured")  # _fit_launcher already refused.
    request = BrowserLaunch(
        layout=work.layout,
        headed=headed,
        sandbox=work.deps.config.browser_sandbox,
        environment=dict(work.environment),
    )
    launched = await launcher.launch(work.session, request)
    work.started.extend(launched.processes)
    return launched.browser


def _buzz(work: _Attaching, server: PulseServer | None) -> PulseAudioBuzz | None:
    """Build the Buzz over the lease's sound server, when one was started."""
    return PulseAudioBuzz(work.session, server) if server is not None else None


def _attached_payload(plan: AttachPlan, started: list[BackgroundProcess]) -> dict[str, JsonValue]:
    """Describe an attach for the trail: names and counts, never a frame, URL or path."""
    return {
        "peripherals": list(plan.peripherals()),
        "display": plan.display.value,
        "processes": len(started),
    }
