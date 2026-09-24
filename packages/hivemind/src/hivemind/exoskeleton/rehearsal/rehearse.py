"""Replay a BrowserProcedure on an attached browser and report whether it still does its job.

ADR-0032: before a browser procedure is promoted as a tool (the Royal Jelly Lab, 9.3), it is
rehearsed against a fixture or staging copy of its site, and the `RehearsalReport` is what the
promotion asks for. `rehearse` runs each action's steps in order on the attached peripherals, then
observes its postconditions for their settle time, exactly as the Capping gate would have; it
stops at the first action that fails, since every later action would act on the wrong page. A
rehearsal stays on its site: a navigation to any origin but the procedure's own fails the action
instead of leaving (`rebase` a procedure onto the fixture's origin first). Secret steps are filled
from the values the caller hands in, by slot name, and those values never reach the report.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside
    `hivemind.exoskeleton.rehearsal`. Called by the `hive recordings rehearse` command. Calls into
    `hivemind.exoskeleton.attach` (Peripherals, Deadline), `.errors`, `.surface` (run_step,
    observe_until), `rehearsal.procedure`, `hivemind.supervision.capping` (PostconditionOutcome)
    and waggle only.

Key invariants:
    - Nothing in a RehearsalReport holds a secret: step failures name the peripheral and the
      operation, and every observation is scrubbed at the source.
    - A rehearsal never navigates off `procedure.origin`.

See Also:
    - hivemind.exoskeleton.rehearsal.procedure for export and rebase.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from hivemind.exoskeleton.attach import Peripherals
from hivemind.exoskeleton.attach.ready import Deadline
from hivemind.exoskeleton.errors import PeripheralError, ProcedureError
from hivemind.exoskeleton.rehearsal.procedure import BrowserProcedure, ProcedureAction, origin_of
from hivemind.exoskeleton.surface import (
    DEFAULT_SETTLE_S,
    SETTLE_POLL_S,
    observe_until,
    run_step,
)
from hivemind.supervision.capping import PostconditionOutcome
from waggle.clock import Clock
from waggle.messages.base import UtcDatetime
from waggle.messages.capping import GuiOp, GuiStep

__all__ = ["RehearsalReport", "RehearsedAction", "rehearse"]

_FROZEN = ConfigDict(frozen=True, extra="forbid")


@dataclass(frozen=True, slots=True)
class _Stage:
    """What every action of one rehearsal shares: the site, the peripherals, the clock."""

    origin: str
    peripherals: Peripherals
    clock: Clock
    settle_s: float


class RehearsedAction(BaseModel):
    """How one action of a procedure went: its steps, then its postconditions."""

    model_config = _FROZEN

    index: int = Field(ge=0, description="The action's position in the procedure.")
    steps_applied: int = Field(ge=0, description="Steps that ran before any failure.")
    failure: str | None = Field(default=None, description="Why a step failed; None if none did.")
    postconditions: tuple[PostconditionOutcome, ...] = Field(default=())

    @property
    def passed(self) -> bool:
        """Every step ran and every postcondition held."""
        return self.failure is None and all(pc.has_held for pc in self.postconditions)


class RehearsalReport(BaseModel):
    """One rehearsal of a procedure: where, when, and action by action how it went."""

    model_config = _FROZEN

    procedure: str = Field(description="The procedure's name.")
    recording_id: str = Field(description="The recording the procedure was exported from.")
    site: str = Field(description="The origin it was rehearsed on.")
    passed: bool = Field(description="Every action ran and every postcondition held.")
    actions: tuple[RehearsedAction, ...] = Field(description="Up to and including a failure.")
    started_at: UtcDatetime = Field(description="When the rehearsal began.")
    finished_at: UtcDatetime = Field(description="When it ended.")


async def rehearse(
    procedure: BrowserProcedure,
    peripherals: Peripherals,
    clock: Clock,
    secrets: Mapping[str, str],
    settle_s: float = DEFAULT_SETTLE_S,
) -> RehearsalReport:
    """Replay `procedure` on `peripherals` and report how every action went.

    Args:
        procedure: What to replay, already `rebase`d onto the site to rehearse on.
        peripherals: An attached Exoskeleton with a browser.
        clock: Stamps the report and bounds every settle wait.
        secrets: A value for every one of the procedure's secret slots, by slot name.
        settle_s: How long each postcondition may take to hold.

    Returns:
        The report; `passed` only when every action ran and all its postconditions held.

    Raises:
        ProcedureError: A secret slot has no value, or no browser is attached.
    """
    missing = sorted(slot.name for slot in procedure.secrets if slot.name not in secrets)
    if missing:
        raise ProcedureError(f"no value was handed in for {', '.join(missing)}")
    if peripherals.browser is None:
        raise ProcedureError("no browser is attached to rehearse on")
    stage = _Stage(procedure.origin, peripherals, clock, settle_s)
    started = clock.now()
    results: list[RehearsedAction] = []
    for index, action in enumerate(procedure.actions):
        steps = _filled(procedure, index, action, secrets)
        results.append(await _rehearse_one(stage, index, steps, action))
        if not results[-1].passed:
            break  # Every later action would act on a page this one failed to reach.
    passed = len(results) == len(procedure.actions) and all(r.passed for r in results)
    return RehearsalReport(
        procedure=procedure.name,
        recording_id=procedure.recording_id,
        site=procedure.origin,
        passed=passed,
        actions=tuple(results),
        started_at=started,
        finished_at=clock.now(),
    )


def _filled(
    procedure: BrowserProcedure, index: int, action: ProcedureAction, secrets: Mapping[str, str]
) -> tuple[GuiStep, ...]:
    """Return `action`'s steps with each secret slot's value typed in place of the mask."""
    values = {slot.step: secrets[slot.name] for slot in procedure.secrets if slot.action == index}
    return tuple(
        step.model_copy(update={"text": values[at]}) if at in values else step
        for at, step in enumerate(action.steps)
    )


async def _rehearse_one(
    stage: _Stage, index: int, steps: tuple[GuiStep, ...], action: ProcedureAction
) -> RehearsedAction:
    """Run one action's steps, stopping at the first failure, then observe its postconditions."""
    for at, step in enumerate(steps):
        failure = _off_site(step, stage.origin)
        if failure is None:
            try:
                await run_step(stage.peripherals, step)
            except PeripheralError as error:
                failure = error.reason  # Names the peripheral's trouble, never typed text.
        if failure is not None:
            reason = f"step {at + 1} ({step.op.value}) failed: {failure}"
            return RehearsedAction(index=index, steps_applied=at, failure=reason)
    outcomes = []
    for at, postcondition in enumerate(action.postconditions):
        deadline = Deadline.after(stage.clock, stage.settle_s, interval=SETTLE_POLL_S)
        seen = await observe_until(stage.peripherals, postcondition, {}, deadline)
        outcomes.append(
            PostconditionOutcome(
                index=at, kind=postcondition.kind, has_held=seen.held, observed=seen.observed
            )
        )
    return RehearsedAction(index=index, steps_applied=len(steps), postconditions=tuple(outcomes))


def _off_site(step: GuiStep, origin: str) -> str | None:
    """Why `step` would leave the rehearsal's site, or None when it stays on it."""
    if step.op is not GuiOp.NAVIGATE or step.url is None or origin_of(step.url) == origin:
        return None
    return f"it navigates off the rehearsal site, to {origin_of(step.url)}"
