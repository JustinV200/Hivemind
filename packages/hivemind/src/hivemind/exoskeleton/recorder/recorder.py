"""Define FlightRecorder: one attach's recording, one RecordedAction per GUI proposal.

While an Exoskeleton is attached, every GUI proposal the Capping gate handles is recorded (roadmap
step 6.6, ADR-0032). The gate's GUI surface calls `begin` with the proposal and the evidence it
took before applying, and `end` once the proposal is terminal, with the evidence after, what each
GUI postcondition observed, and how it was rolled back; `end` builds the `RecordedAction` (steps
described with secrets as a length, text scrubbed) and appends it to the store. A proposal the
gate rejected before it was ever applied is still recorded, with no evidence: an attempt is
evidence too. `open` writes the recording's header once, at attach.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside
    `hivemind.exoskeleton.recorder`. Built at attach time by whoever attaches (the Warden's
    `equip`) and driven by `hivemind.exoskeleton.surface.ExoskeletonSurface`. Calls into
    `recorder.models`, `.redact`, `.store`, `hivemind.supervision.capping` (Proposal) and waggle.

Key invariants:
    - Nothing recorded ever holds typed secret text: steps go through `GuiStep.describe()`,
      expected values and snapshots through `scrub_text`, URLs through `scrub_url`.
    - Owns mutable state (codingrules 8.5): the proposals begun and not yet ended, keyed by id.

See Also:
    - hivemind.exoskeleton.recorder.models for what is kept.
    - hivemind.exoskeleton.recorder.store for where.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from hivemind.exoskeleton.frames import Frame
from hivemind.exoskeleton.recorder.models import (
    MAX_SNAPSHOT_CHARS,
    Evidence,
    RecordedAction,
    RecordedPostcondition,
    RecordingInfo,
)
from hivemind.exoskeleton.recorder.redact import MASK, scrub_text, scrub_url
from hivemind.exoskeleton.recorder.store import RecordingStore
from hivemind.supervision.capping import Proposal
from waggle.clock import Clock
from waggle.messages.capping import RollbackMethod

MAX_EXPECTED_CHARS = 500  # An expected value is a phrase or a URL; past this it is cut.

__all__ = ["MAX_EXPECTED_CHARS", "FlightRecorder", "scrubbed_evidence"]


@dataclass(frozen=True, slots=True)
class _Begun:
    """A proposal whose before-evidence is taken and whose outcome is not yet known."""

    before: Evidence
    started_at: datetime


class FlightRecorder:
    """Record one attach's GUI proposals into a RecordingStore."""

    def __init__(self, store: RecordingStore, info: RecordingInfo, clock: Clock) -> None:
        """Build the recorder for one recording.

        Args:
            store: Where the header and every action go.
            info: This recording's header.
            clock: Stamps each action's start and finish.
        """
        self._store = store
        self._info = info
        self._clock = clock
        self._begun: dict[str, _Begun] = {}

    @property
    def recording_id(self) -> str:
        """This recording's id, for the Bee Bread entry and an Alarm that names it."""
        return self._info.recording_id

    async def open(self) -> None:
        """Write the recording's header; call once, at attach."""
        await self._store.open(self._info)

    def begin(self, proposal: Proposal, before: Evidence) -> None:
        """Note the evidence taken just before `proposal`'s steps run.

        Args:
            proposal: The capped proposal.
            before: The screen and page before applying, already scrubbed.
        """
        self._begun[str(proposal.id)] = _Begun(before=before, started_at=self._clock.now())

    async def end(
        self,
        proposal: Proposal,
        after: Evidence | None,
        postconditions: tuple[RecordedPostcondition, ...],
        rollback: RollbackMethod | None,
    ) -> RecordedAction:
        """Record `proposal` in its terminal state and return what was stored.

        Args:
            proposal: The proposal, terminal (VERIFIED, REJECTED or ROLLED_BACK).
            after: The evidence once the gate was done; None for one never applied.
            postconditions: What the surface observed for each declared postcondition.
            rollback: How it was rolled back, when it was.

        Returns:
            The RecordedAction appended to the store.
        """
        begun = self._begun.pop(str(proposal.id), None)
        now = self._clock.now()
        action = RecordedAction(
            proposal_id=str(proposal.id),
            tier=proposal.risk_tier.value,
            steps=tuple(step.describe() for step in proposal.action.gui),
            before=begun.before if begun is not None else Evidence(),
            after=after,
            postconditions=postconditions,
            state=proposal.state.value,
            rollback=rollback.value if rollback is not None else None,
            started_at=begun.started_at if begun is not None else now,
            finished_at=now,
        )
        await self._store.add(self._info.recording_id, action)
        return action


def scrubbed_evidence(
    frame: Frame | None, url: str | None, snapshot: str | None, secrets: tuple[str, ...] = ()
) -> Evidence:
    """Build Evidence with its URL and snapshot scrubbed, the one way the surface makes it.

    Args:
        frame: The captured Frame, or None.
        url: The page URL as the browser reported it, or None.
        snapshot: The page's accessibility snapshot, or None.
        secrets: Text the proposal typed as secret; removed wherever it appears, in case a page
            echoes a field's value into its accessibility tree or its URL.

    Returns:
        Evidence safe to store.
    """
    return Evidence(
        frame=frame,
        url=scrub_url(_without(url, secrets)) if url is not None else None,
        snapshot=(
            scrub_text(_without(snapshot, secrets), MAX_SNAPSHOT_CHARS)
            if snapshot is not None
            else None
        ),
    )


def _without(text: str, secrets: tuple[str, ...]) -> str:
    """Replace every occurrence of each secret in `text` with the mask."""
    for secret in secrets:
        text = text.replace(secret, MASK)
    return text
