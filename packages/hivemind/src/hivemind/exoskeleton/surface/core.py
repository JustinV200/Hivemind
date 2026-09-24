"""Define ExoskeletonSurface: the Capping gate's GuiSurface over one attached Exoskeleton.

The gate applies, verifies and undoes a GUI proposal only through a `GuiSurface` (Layer 2,
`hivemind.supervision.capping.gui`, ADR-0032); this is the implementation over an attached
handle's peripherals, with the flight recorder beside it. For each proposal it keeps a little state
between the gate's calls: `before` takes the browser's undo point (URL, cookies, local storage),
the region digests the declared REGION_CHANGED postconditions need, and the before-evidence (a
frame, the page URL and accessibility snapshot); `apply` runs the steps in order and stops at the
first failure; `check` observes one GUI postcondition until it holds or its settle time passes;
`restore` puts the undo point back; `finish` takes the after-evidence and records the action; and
`evidence` hands a judge what was recorded for an action it must review (`surface.evidence`).
Evidence is best effort: a capture that fails is left out and logged, never fails the proposal.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside
    `hivemind.exoskeleton.surface`. Built by the Warden's `equip` for a sub-bee whose task has an
    Exoskeleton, and injected into that sub-bee's `GateDeps.gui`. Calls into
    `hivemind.common.logging`, `hivemind.supervision.capping` (Proposal, PostconditionOutcome,
    GuiApplyResult, JudgeEvidence), the exoskeleton's `attach`, `browser`, `errors`, `recorder`
    and this package's `steps`, `verify` and `evidence`.

Key invariants:
    - Nothing here decides whether a step may run; the gate already checked it.
    - Owns mutable state (codingrules 8.5): the per-proposal `_Pending` records, created by
      `before` (or by `finish` for a proposal rejected before it), dropped by `finish`; and the
      last KEPT_FOR_REVIEW recorded actions, oldest dropped first, for `evidence`.

See Also:
    - hivemind.supervision.capping.gui for the protocol and when the gate calls each method.
    - hivemind.exoskeleton.recorder for what is recorded.
"""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass, field

from hivemind.common.logging import get_logger
from hivemind.exoskeleton.attach import Peripherals
from hivemind.exoskeleton.attach.ready import Deadline
from hivemind.exoskeleton.browser.base import BrowserCheckpoint
from hivemind.exoskeleton.errors import PeripheralError
from hivemind.exoskeleton.frames import Frame
from hivemind.exoskeleton.geometry import Region
from hivemind.exoskeleton.recorder import (
    Evidence,
    FlightRecorder,
    RecordedAction,
    RecordedPostcondition,
    scrub_text,
    scrubbed_evidence,
)
from hivemind.exoskeleton.surface.evidence import judge_evidence
from hivemind.exoskeleton.surface.steps import run_step
from hivemind.exoskeleton.surface.verify import observe_until
from hivemind.supervision.capping import (
    GuiApplyResult,
    JudgeEvidence,
    PostconditionOutcome,
    Proposal,
)
from waggle.clock import Clock
from waggle.messages.capping import RollbackMethod
from waggle.messages.labels import Postcondition, PostconditionKind

DEFAULT_SETTLE_S = 5.0  # How long a page may take to react before a postcondition counts as failed.
SETTLE_POLL_S = 0.2  # A check may capture the screen, so it polls five times a second, not twenty.
KEPT_FOR_REVIEW = 8  # Recorded actions kept in memory for a judge; the store keeps them all.
_MAX_EXPECTED_CHARS = 500  # What the recorder keeps of an expected value.

log = get_logger(__name__)

__all__ = ["DEFAULT_SETTLE_S", "SETTLE_POLL_S", "ExoskeletonSurface"]


@dataclass
class _Pending:
    """One proposal between `before` and `finish`: its undo point, digests and observations."""

    checkpoint: BrowserCheckpoint | None = None
    digests: dict[str, str] = field(default_factory=dict)
    observed: dict[int, RecordedPostcondition] = field(default_factory=dict)
    applied: bool = False


class ExoskeletonSurface:
    """Apply, verify, undo and record GUI proposals on one attached Exoskeleton."""

    def __init__(
        self,
        peripherals: Peripherals,
        clock: Clock,
        recorder: FlightRecorder | None = None,
        settle_s: float = DEFAULT_SETTLE_S,
    ) -> None:
        """Build the surface over what one attach provided.

        Args:
            peripherals: The handle's peripherals.
            clock: Bounds every settle wait.
            recorder: Where each proposal is recorded; None records nothing.
            settle_s: The settle time of every GUI postcondition.
        """
        self._peripherals = peripherals
        self._clock = clock
        self._recorder = recorder
        self._settle_s = settle_s
        self._pending: dict[str, _Pending] = {}
        self._recorded: dict[str, RecordedAction] = {}  # The last few, for a judge's review.

    @property
    def recording_id(self) -> str | None:
        """The recording every proposal on this surface is recorded into; None without one."""
        return self._recorder.recording_id if self._recorder is not None else None

    async def before(self, proposal: Proposal) -> None:
        """Take the undo point, the region digests and the before-evidence; see GuiSurface."""
        pending = self._pending.setdefault(str(proposal.id), _Pending())
        browser = self._peripherals.browser
        if browser is not None:
            pending.checkpoint = await _best_effort(browser.checkpoint(), "checkpoint")
        for pc in proposal.postconditions:
            if pc.kind is PostconditionKind.REGION_CHANGED:
                digest = await self._digest(pc.subject)
                if digest is not None:
                    pending.digests[pc.subject] = digest
        if self._recorder is not None:
            self._recorder.begin(proposal, await self._evidence(proposal))

    async def apply(self, proposal: Proposal) -> GuiApplyResult:
        """Run the steps in order, stopping at the first failure; see GuiSurface."""
        self._pending.setdefault(str(proposal.id), _Pending()).applied = True
        for index, step in enumerate(proposal.action.gui):
            try:
                await run_step(self._peripherals, step)
            except PeripheralError as error:
                # The reason names the peripheral and operation, never typed text (errors.py).
                reason = f"step {index + 1} ({step.op.value}) failed: {error.reason}"
                return GuiApplyResult(succeeded=False, steps_applied=index, failure_reason=reason)
        steps = len(proposal.action.gui)
        return GuiApplyResult(succeeded=True, steps_applied=steps)

    async def check(
        self, proposal: Proposal, index: int, postcondition: Postcondition
    ) -> PostconditionOutcome:
        """Observe one GUI postcondition until it holds or settles; see GuiSurface."""
        pending = self._pending.setdefault(str(proposal.id), _Pending())
        deadline = Deadline.after(self._clock, self._settle_s, interval=SETTLE_POLL_S)
        seen = await observe_until(self._peripherals, postcondition, pending.digests, deadline)
        pending.observed[index] = _recorded(postcondition, seen.held, seen.observed)
        return PostconditionOutcome(
            index=index, kind=postcondition.kind, has_held=seen.held, observed=seen.observed
        )

    async def restore(self, proposal: Proposal) -> bool:
        """Put the browser's undo point back; see GuiSurface."""
        pending = self._pending.get(str(proposal.id))
        browser = self._peripherals.browser
        if pending is None or pending.checkpoint is None or browser is None:
            return False  # A desktop-only proposal has no browser state to put back.
        try:
            await browser.restore(pending.checkpoint)
        except PeripheralError as error:
            log.warning("exoskeleton.restore_failed", reason=error.reason)
            return False
        return True

    async def finish(self, proposal: Proposal, rollback: RollbackMethod | None) -> None:
        """Take the after-evidence and record the proposal; see GuiSurface."""
        pending = self._pending.pop(str(proposal.id), _Pending())
        if self._recorder is None:
            return
        after = await self._evidence(proposal) if pending.applied else None
        outcomes = tuple(
            pending.observed.get(i, _recorded(pc, None, ""))
            for i, pc in enumerate(proposal.postconditions)
        )
        action = await self._recorder.end(proposal, after, outcomes, rollback)
        self._recorded[str(proposal.id)] = action
        # Only the latest few are ever judged (the one just applied); older ones live in the store.
        while len(self._recorded) > KEPT_FOR_REVIEW:
            del self._recorded[next(iter(self._recorded))]

    async def evidence(self, proposal: Proposal) -> JudgeEvidence | None:
        """Return what was recorded for `proposal`, rendered for a judge; see GuiSurface."""
        action = self._recorded.get(str(proposal.id))
        return judge_evidence(action) if action is not None else None

    async def _digest(self, subject: str) -> str | None:
        """Fingerprint one region before the action; None when it cannot be captured."""
        eye = self._peripherals.compound_eye
        if eye is None:
            return None
        return await _best_effort(eye.region_digest(Region.parse(subject)), "digest")

    async def _evidence(self, proposal: Proposal) -> Evidence:
        """Capture the screen, and the page when there is one, scrubbed of the proposal's secrets.

        Best effort: a capture that fails is left out of the evidence rather than failing it.
        """
        eye, browser = self._peripherals.compound_eye, self._peripherals.browser
        frame: Frame | None = None
        if eye is not None:
            frame = await _best_effort(eye.capture(), "capture")
        elif browser is not None:
            frame = await _best_effort(browser.screenshot(), "screenshot")
        url = await _best_effort(browser.url(), "url") if browser is not None else None
        snapshot = await _best_effort(browser.snapshot(), "snapshot") if browser else None
        secrets = tuple(
            step.text for step in proposal.action.gui if step.secret and step.text is not None
        )
        return scrubbed_evidence(frame, url, snapshot, secrets)


def _recorded(pc: Postcondition, held: bool | None, observed: str) -> RecordedPostcondition:
    """Build the recorder's view of one postcondition: kind, subject, scrubbed expectation."""
    expected = scrub_text(pc.expected, _MAX_EXPECTED_CHARS) if pc.expected is not None else None
    return RecordedPostcondition(
        kind=pc.kind.value, subject=pc.subject, expected=expected, has_held=held, observed=observed
    )


async def _best_effort[ResultT](awaitable: Awaitable[ResultT], what: str) -> ResultT | None:
    """Await one evidence capture; a peripheral failure leaves it out rather than failing."""
    try:
        return await awaitable
    except PeripheralError as error:
        log.debug("exoskeleton.evidence_missing", what=what, reason=error.reason)
        return None
