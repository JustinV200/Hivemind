"""Define ExoskeletonHandle: what one task holds while its Exoskeleton is attached, and detach.

Attach (`hivemind.exoskeleton.attach.core`) returns one handle per task. It carries the peripherals
the plan provided (a CompoundEye and Antennae for a desktop, a Buzz for audio, a Browser for the
fast path), the environment a lease-started application needs to find them (HOME in scratch, the
display, the sound server), and the processes attach started. `detach` stops exactly those
processes, newest first, verifies each is gone, and records `cell.exoskeleton_detached`; it never
stops a process it did not start, so a borrowed display, the operator's own, survives untouched.
`ExoskeletonTrail` records the two trail events with peripheral names and counts, never a frame.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.exoskeleton.attach`.
    Built by `attach.core.attach`; held by the Worker's Exoskeleton tools and the Capping gate's
    GUI surface for one task, and detached by whoever attached it when the task ends. Calls into
    `hivemind.cell` (CellSession, BackgroundProcess, CellIdentity), `hivemind.pheromone`
    (CellEvent, PheromoneTrail), the peripheral protocols and `attach.plan`, `attach.ready` only.

Key invariants:
    - `detach` is idempotent: a second call returns the first call's report and records nothing.
    - Neither trail event carries a frame, a recording, a URL or typed text.

See Also:
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md, "Attach is a pure plan plus
      a handle".
    - hivemind.pheromone.events.families for the two event kinds.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from pydantic import JsonValue

from hivemind.cell import BackgroundProcess, CellIdentity, CellSession
from hivemind.common.logging import get_logger
from hivemind.exoskeleton.antennae.base import Antennae
from hivemind.exoskeleton.attach.plan import AttachPlan
from hivemind.exoskeleton.attach.ready import stop_all
from hivemind.exoskeleton.browser.base import Browser
from hivemind.exoskeleton.buzz.base import Buzz
from hivemind.exoskeleton.compound_eye.base import CompoundEye
from hivemind.exoskeleton.errors import PeripheralError
from hivemind.pheromone import CellEvent, PheromoneTrail
from waggle.clock import Clock
from waggle.ids import CellId, new_event_id

log = get_logger(__name__)

__all__ = ["DetachReport", "ExoskeletonHandle", "ExoskeletonTrail", "Peripherals"]


@dataclass(frozen=True, slots=True)
class Peripherals:
    """The peripherals one attach provided; None for each the plan did not include."""

    compound_eye: CompoundEye | None = None
    antennae: Antennae | None = None
    buzz: Buzz | None = None
    browser: Browser | None = None


@dataclass(frozen=True, slots=True)
class DetachReport:
    """What detach found: how many processes it stopped, and how many would not stop."""

    stopped: int  # Running when detach reached them, and stopped.
    still_running: int  # Still running after their stop: a leak the caller must raise an alarm on.


@dataclass(frozen=True, slots=True)
class ExoskeletonTrail:
    """Records the Exoskeleton's two trail events for one Cell."""

    trail: PheromoneTrail
    identity: CellIdentity
    clock: Clock
    cell_id: CellId

    async def record(self, kind: str, payload: Mapping[str, JsonValue]) -> None:
        """Append one `cell.exoskeleton_*` event about this Cell.

        Args:
            kind: "cell.exoskeleton_attached" or "cell.exoskeleton_detached".
            payload: Names and counts only (module docstring).
        """
        event = CellEvent(
            id=new_event_id(self.clock),
            hive_id=self.identity.hive_id,
            node_id=self.identity.node_id,
            at=self.clock.now(),
            actor=self.identity.actor,
            kind=kind,
            subject_id=self.cell_id,
            payload=dict(payload),
        )
        await self.trail.record(event)


@dataclass
class ExoskeletonHandle:
    """One task's attached Exoskeleton: its plan, peripherals and environment, and `detach`."""

    plan: AttachPlan
    peripherals: Peripherals
    environment: Mapping[str, str]  # HOME in scratch, DISPLAY/XAUTHORITY, PULSE_* as attached.
    _session: CellSession
    _processes: tuple[BackgroundProcess, ...]
    _trail: ExoskeletonTrail
    _report: DetachReport | None = field(default=None)

    @property
    def processes(self) -> tuple[BackgroundProcess, ...]:
        """Every process attach started for this task, oldest first."""
        return self._processes

    @property
    def detached(self) -> bool:
        """Whether `detach` has run."""
        return self._report is not None

    async def detach(self) -> DetachReport:
        """Stop exactly what attach started, verify it is gone, and record it. Idempotent.

        Returns:
            How many processes were stopped and how many are still running.
        """
        if self._report is not None:
            return self._report
        await self._close_browser()
        stopped = await stop_all(self._session, self._processes)
        still = [p for p in self._processes if await self._session.is_running(p)]
        self._report = DetachReport(stopped=stopped, still_running=len(still))
        payload: dict[str, JsonValue] = {
            "stopped": stopped,
            "still_running": len(still),
            "peripherals": list(self.plan.peripherals()),
        }
        await self._trail.record("cell.exoskeleton_detached", payload)
        return self._report

    async def _close_browser(self) -> None:
        """Disconnect the browser client before its process is stopped under it."""
        browser = self.peripherals.browser
        if browser is None:
            return
        try:
            await browser.close()
        except PeripheralError as error:
            # The process is stopped next either way; a failed disconnect changes nothing.
            log.debug("exoskeleton.browser_close_failed", reason=error.reason)
