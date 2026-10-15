"""Test the gateway's hand on a running exec: one verdict, signals to the group, limits that trip.

When the Pollen Packet (the thin gateway program on a device the Hive borrows) ends an exec itself
it must say why, as KILLED or TIMED_OUT with a reason, and the first reason must stand: a later
cause cannot rewrite what really happened (gateway-contract section 11). Signals that arrive on
stdin go to the program's whole process group, and one that arrives before the program has started
is held and delivered the moment it has. The watchdog ends a run that outlives its timeout or
whose scratch directory outgrows its quota. These tests start real programs through the platform
shim, with a real clock whose sleeps are cut short, and read the verdicts the control reports.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.executors.run.control.

Key invariants:
    - A test that starts a program also reaps it, whatever the test does.
    - Every limit is exercised against a real process and a real directory.

See Also:
    - docs/waggle/gateway-contract.md section 11.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from executor_kit import Kit, Limits, make_kit, make_plan, open_lease_for
from fast_clock import FastClock

from pollen.executors.run.control import MAX_SIGNAL_NAME_CHARS, RunControl
from pollen.lease import DeviceLease
from pollen.platform.base import SpawnSpec
from waggle.messages.session import ProcessSignal, SessionOutcome

CLOCK = FastClock()
LIMIT_S = 20.0  # A generous bound on a real child, so a hang fails instead of stalling the suite.
SLEEPER = "import time; time.sleep(60)"
# What a forced kill leaves as the return code: SIGKILL's -9 on POSIX, and on Windows the exit
# code 1 that `taskkill /F` ends a process with (Windows has no signal 9, so -9 is named SIG9).
KILLED_RETURNCODE = 1 if sys.platform == "win32" else -9
NINE_NAME = "SIG9" if sys.platform == "win32" else "SIGKILL"


class Rig:
    """Starts real programs the way the runner does, and reaps whatever a test leaves."""

    def __init__(self, kit: Kit, lease: DeviceLease) -> None:
        """Bind the rig to a kit and the lease its programs run in."""
        self.kit = kit
        self.lease = lease
        self._children: list[asyncio.subprocess.Process] = []

    def control(self, limits: Limits | None = None) -> RunControl:
        """Build a control for an exec in the lease, under ``limits``."""
        argv = (sys.executable, "-c", "pass")
        return RunControl(make_plan(self.kit, CLOCK, self.lease, argv, limits), self.kit.deps)

    async def start(
        self, control: RunControl, code: str, *, attach: bool = True
    ) -> asyncio.subprocess.Process:
        """Start ``code`` as a group leader in the lease's scratch, optionally handing it over."""
        spec = SpawnSpec(
            argv=(sys.executable, "-c", code),
            cwd=Path(self.lease.scratch_root),
            env=dict(os.environ),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        shim = self.kit.deps.shim
        process = await shim.spawn(spec)
        self._children.append(process)
        if attach:
            control.attach(shim.process_ref(process.pid))
        return process

    async def close(self) -> None:
        """Kill and reap every program a test left running."""
        for process in self._children:
            if process.returncode is None:
                process.kill()
            await process.wait()


@pytest.fixture
async def rig(tmp_path: Path) -> AsyncIterator[Rig]:
    """A kit with an open lease, and a rig that reaps what the test starts."""
    kit = make_kit(tmp_path, CLOCK)
    holder = Rig(kit, await open_lease_for(kit, CLOCK, quota=1_000_000))
    yield holder
    await holder.close()


async def _finish(process: asyncio.subprocess.Process) -> int:
    """Wait for a program to end, with a bound, and return its return code."""
    return await asyncio.wait_for(process.wait(), timeout=LIMIT_S)


def test_the_first_reason_to_end_a_run_is_the_one_reported(rig: Rig) -> None:
    control = rig.control()
    before = (control.has_verdict, control.is_killing)
    control.set_verdict(SessionOutcome.TIMED_OUT, "first")
    control.set_verdict(SessionOutcome.KILLED, "second")
    assert before == (False, False) and control.has_verdict
    assert control.result(-9) == (SessionOutcome.TIMED_OUT, None, None, "first")


@pytest.mark.parametrize(
    ("returncode", "expected"),
    [
        (0, (SessionOutcome.EXITED, 0, None, "The program exited.")),
        (3, (SessionOutcome.EXITED, 3, None, "The program exited.")),
        (-15, (SessionOutcome.SIGNALED, None, "SIGTERM", "A signal ended it.")),
        (-9, (SessionOutcome.SIGNALED, None, NINE_NAME, "A signal ended it.")),
        (-250, (SessionOutcome.SIGNALED, None, "SIG250", "A signal ended it.")),  # No such signal.
    ],
)
def test_without_a_verdict_the_return_code_decides_the_outcome(
    rig: Rig, returncode: int, expected: tuple[SessionOutcome, int | None, str | None, str]
) -> None:
    assert rig.control().result(returncode) == expected
    assert len(expected[2] or "") <= MAX_SIGNAL_NAME_CHARS


async def test_a_signal_before_the_program_started_is_held_then_delivered(rig: Rig) -> None:
    control = rig.control()
    control.deliver(ProcessSignal.TERMINATE)
    assert not control.has_verdict  # There was no process to signal yet.
    process = await rig.start(control, SLEEPER, attach=False)
    control.attach(rig.kit.deps.shim.process_ref(process.pid))
    returncode = await _finish(process)
    assert control.result(returncode) == (
        SessionOutcome.KILLED,
        None,
        None,
        "A stdin signal ended it.",
    )


async def test_a_signal_after_the_program_started_reaches_it_and_is_the_reason(rig: Rig) -> None:
    control = rig.control()
    process = await rig.start(control, SLEEPER)
    control.deliver(ProcessSignal.KILL)
    assert control.has_verdict
    assert await _finish(process) == KILLED_RETURNCODE


async def test_hitting_the_output_cap_kills_the_group_and_says_so(rig: Rig) -> None:
    control = rig.control()
    process = await rig.start(control, SLEEPER)
    control.on_output_cap()
    returncode = await _finish(process)
    outcome, code, name, reason = control.result(returncode)
    assert (outcome, code, name, returncode) == (
        SessionOutcome.KILLED,
        None,
        None,
        KILLED_RETURNCODE,
    )
    assert "cap" in reason


async def test_killing_before_the_program_started_changes_nothing(rig: Rig) -> None:
    control = rig.control()
    control.kill_now()
    assert await control.end_group(graceful=True) is False
    assert not control.has_verdict


@pytest.mark.parametrize("graceful", [True, False])
async def test_ending_a_group_says_whether_it_was_alive(rig: Rig, graceful: bool) -> None:
    control = rig.control()
    process = await rig.start(control, SLEEPER)
    assert await control.end_group(graceful=graceful) is True
    await _finish(process)
    assert await control.end_group(graceful=graceful) is False  # Nothing left to end.


async def test_a_run_past_its_timeout_is_ended_as_timed_out(rig: Rig) -> None:
    control = rig.control(Limits(timeout_s=0.3))
    process = await rig.start(control, SLEEPER)
    await asyncio.wait_for(control.watch(CLOCK.monotonic()), timeout=LIMIT_S)
    assert control.is_killing  # The run must wait for the kill rather than cancel it.
    returncode = await _finish(process)
    assert control.result(returncode) == (
        SessionOutcome.TIMED_OUT,
        None,
        None,
        "It ran past its 0.3 s timeout.",
    )


async def test_scratch_that_outgrows_its_quota_ends_the_run_as_killed(rig: Rig) -> None:
    control = rig.control(Limits(quota_bytes=100, timeout_s=LIMIT_S * 3))
    code = "import time; open('big', 'wb').write(bytes(4096)); time.sleep(60)"
    process = await rig.start(control, code)
    await asyncio.wait_for(control.watch(CLOCK.monotonic()), timeout=LIMIT_S)
    returncode = await _finish(process)
    assert control.result(returncode) == (
        SessionOutcome.KILLED,
        None,
        None,
        "Scratch passed its quota of 100 bytes.",
    )
    # A quota breach is forced at once, with no polite request first.
    assert returncode == KILLED_RETURNCODE
