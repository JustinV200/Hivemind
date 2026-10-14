"""Give a Patrol its Warden: the episode, the Alarm, the Nectar, the Wax and the record.

`hivemind.wardens.watch.patrol.Patrol` decides nothing about the world; it hands a window to a port.
`WardenPatrolPort` is that port for a real Warden, and the only part of the watch package that
reaches into the Warden's machinery, which is why `hivemind.wardens.deps` (whose import closure
must stay light) never imports it. `decide` scans what the machine said (a process name or a log
line is outside text, so it is scored like any tool result), fences it into the Patrol's event
and runs the episode on the Warden's own model slot. `carry_out` does what the decision says
through the Warden's existing doors, each already guarded: the Alarm through
`ticks.alarms.send_alarm_to_queen` (`AlarmKind.PATROL_ANOMALY`), the summary as `PATROL_SUMMARY`
Nectar through `ticks.basket.door.send_nectar` (scoped to this Cell by the Honey Store), the caution
as a `CellWaxProposed` of origin `PATROL` (never a BLOCK: only the Queen excludes a Cell). The
Alarm and the Wax proposal describe themselves by counts and times only (`digest.counts_of`),
because both reach the trail and the Queen's records, and a trail payload never carries what the
machine said; the model's own words travel only in the Nectar and the Wax text, both `C2`, and in
the Warden's `C2` episode record. `record` writes the `warden.patrol` event: the outcome and the
counts, no text. `last_patrols` reads that event back, so a restarted Warden does not review an
hour it already reviewed.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    watch sub-package. Built by `hivemind.wardens.watch.tick` for each Patrol. Calls into
    `hivemind.wardens.awake`, `hivemind.wardens.ticks` (alarms and the Basket door),
    `hivemind.wardens.links`, `hivemind.guard.scanner`, `hivemind.memory`, `hivemind.pheromone`,
    `hivemind.workers.nectar` and waggle.

Key invariants:
    - An Alarm and a Wax proposal carry counts and times, never a name, path or line.
    - A Patrol's wax is a NOTE or a CAUTION, `origin=PATROL`, `C2`, about this Warden's own Cell.
    - Nectar is `PATROL_SUMMARY`, `C2`, sent through the Warden's door; a lost chunk makes the
      Patrol SKIPPED, not decided.
    - The `warden.patrol` payload is the outcome and two counts.

See Also:
    - docs/adr/0054-access-levels-watch-mode-and-the-patrol.md for the four outcomes.
    - hivemind.wardens.watch.patrol for the Patrol that drives this port.
    - hivemind.wardens.awake.patrol for the episode `decide` runs.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from hivemind.cell import Cell, HoneyClearance
from hivemind.guard.scanner import ScanRecorder, ScanSite, ScanSource
from hivemind.memory import TriggerEvent, UntrustedText
from hivemind.pheromone import TrailQuery, WardenEvent
from hivemind.wardens.awake import PATROL_TRIGGER_KIND, decide_patrol
from hivemind.wardens.links import send_guarded
from hivemind.wardens.ticks.alarms import send_alarm_to_queen
from hivemind.wardens.ticks.basket.door import send_nectar
from hivemind.wardens.ticks.basket.events import cell_identity
from hivemind.wardens.watch.decision import PatrolChoice, PatrolDecision, PatrolOutcome
from hivemind.wardens.watch.digest import PatrolWindow, counts_of, facts_of, summary_of
from hivemind.workers.nectar import DepositMeta
from waggle.envelope import wrap
from waggle.ids import new_event_id
from waggle.messages.cell import CellWaxProposed, WaxOrigin, WaxSeverity
from waggle.messages.honey import NectarKind
from waggle.messages.supervision import AlarmKind

if TYPE_CHECKING:
    from hivemind.wardens.warden import Warden

FACTS_LABEL = "watch observations untrusted"  # The fence's label: what it is, and that it is data.
PAYLOAD_REF = "patrol-window"  # The event's reference; the window itself is not stored anywhere.
PATROL_EVENT_KIND = "warden.patrol"
NECTAR_TITLE = "Patrol summary"
NECTAR_MEDIA_TYPE = "text/plain"
SEED_LOOKBACK = 20  # Newest Patrol events read to find the last that reviewed: skips come between.
ALARM_REASON = (
    "A Patrol of this Cell judged that a person should see what it found; its reasoning stays in "
    "this Warden's own memory, which a person with C2 clearance can read."
)

__all__ = [
    "FACTS_LABEL",
    "PATROL_EVENT_KIND",
    "WardenPatrolPort",
    "last_patrols",
]


class WardenPatrolPort:
    """The `PatrolPort` of a real Warden: it thinks on its slot and acts through its doors."""

    def __init__(self, warden: Warden, cell: Cell) -> None:
        """Bind the port to one Warden and the Cell it watches.

        Args:
            warden: The Warden whose deps, ids and Queen link this uses.
            cell: The Cell being watched (its id, its Comb Shield tier).
        """
        self._warden = warden
        self._cell = cell

    async def decide(self, window: PatrolWindow) -> PatrolDecision:
        """Scan what the machine said, fence it into the Patrol's event and run the episode.

        Args:
            window: What this Patrol reviews.

        Returns:
            The Patrol's one decision.

        Raises:
            hivemind.llm.errors.LLMError: No model could be reached or would answer.
        """
        deps = self._warden._deps
        text = facts_of(window)
        site = ScanSite(
            source=ScanSource.SESSION_OUTPUT,  # What a Cell's own processes and files printed.
            consumer=str(self._warden._warden_id),
            recorder=ScanRecorder(trail=deps.trail, identity=cell_identity(deps), clock=deps.clock),
            tier=self._cell.comb_shield,
            ref=PAYLOAD_REF,
        )
        verdict = await deps.scanner.scan(text, site)
        event = TriggerEvent(
            kind=PATROL_TRIGGER_KIND,
            summary=summary_of(window),
            payload_ref=PAYLOAD_REF,
            clearance=HoneyClearance.C2,
            untrusted=UntrustedText(label=FACTS_LABEL, text=text, verdict=verdict),
        )
        return await decide_patrol(deps, event, self._warden._warden_id)

    async def carry_out(self, decision: PatrolDecision, window: PatrolWindow) -> bool:
        """Do what the decision says through the Warden's own doors.

        Args:
            decision: What the Patrol decided.
            window: What it reviewed, for the counts an Alarm or a proposal describes itself by.

        Returns:
            True when it was delivered (NOTHING always); False when the Queen's link could not
            take it.
        """
        if decision.choice is PatrolChoice.ALARM:
            await self._alarm(window)
            return True
        if decision.choice is PatrolChoice.NECTAR:
            return await self._nectar(decision, window)
        if decision.choice is PatrolChoice.WAX:
            return await self._wax(decision, window)
        return True

    async def record(self, outcome: PatrolOutcome, window: PatrolWindow) -> None:
        """Record one `warden.patrol` event: the outcome and two counts, never a word of text.

        Args:
            outcome: How the Patrol ended.
            window: What it reviewed.
        """
        deps = self._warden._deps
        event = WardenEvent(
            id=new_event_id(deps.clock),
            hive_id=deps.identity.hive_id,
            node_id=deps.identity.node_id,
            at=deps.clock.now(),
            actor=deps.identity.actor,
            kind=PATROL_EVENT_KIND,
            subject_id=self._warden._warden_id,
            payload={
                "outcome": outcome.value,
                "observations": len(window.observations),
                "signals": len(window.signals),
            },
        )
        await deps.trail.record(event)

    async def _alarm(self, window: PatrolWindow) -> None:
        """Raise a PATROL_ANOMALY Alarm that says how much was reviewed and nothing it contained."""
        await send_alarm_to_queen(
            self._warden,
            kind=AlarmKind.PATROL_ANOMALY,
            detail=f"A Patrol of this Cell found something to see, over {counts_of(window)}.",
            reason=ALARM_REASON,
        )

    async def _nectar(self, decision: PatrolDecision, window: PatrolWindow) -> bool:
        """Send the model's summary up as C2 PATROL_SUMMARY Nectar."""
        warden, cell = self._warden, self._cell
        meta = DepositMeta(
            kind=NectarKind.PATROL_SUMMARY,
            media_type=NECTAR_MEDIA_TYPE,
            title=NECTAR_TITLE,
            task_id=None,  # A Patrol belongs to no task.
            cell_id=cell.id,
            worker_id=None,  # A Warden deposits this one.
            observed_at=window.until,
            clearance=HoneyClearance.C2.to_wire(),  # Always: it describes the operator's machine.
            origin_tier=cell.comb_shield.to_wire(),
        )
        # A model that chose NECTAR but wrote no text still gave its reason, which says what it saw.
        text = decision.text.strip() or decision.reason
        return await send_nectar(warden, text.encode("utf-8"), meta)

    async def _wax(self, decision: PatrolDecision, window: PatrolWindow) -> bool:
        """Propose the model's caution as C2 Cell Wax: a NOTE unless it said CAUTION."""
        deps = self._warden._deps
        severity = (
            decision.severity.value if decision.severity is not None else WaxSeverity.NOTE.value
        )
        proposal = CellWaxProposed(
            cell_id=self._cell.id,
            severity=WaxSeverity(severity),
            text=decision.text.strip() or decision.reason,
            reason=f"A Patrol of this Cell proposed this note over {counts_of(window)}.",
            clearance=HoneyClearance.C2.to_wire(),
            expires_at=None,
            origin=WaxOrigin.PATROL,
            proposer=str(self._warden._warden_id),
            task_id=None,
        )
        return await send_guarded(deps.queen_link, wrap(proposal, deps.hop, clock=deps.clock))


async def last_patrols(warden: Warden) -> tuple[datetime | None, datetime | None]:
    """Read when this Warden last ran a Patrol, and when it last ran one that reviewed.

    Args:
        warden: The Warden whose `warden.patrol` events are read.

    Returns:
        (the newest Patrol of any outcome, the newest that was not SKIPPED); None for either when
        the trail holds none, which is how a Warden that has never patrolled looks.
    """
    events = await warden._deps.trail.query(
        TrailQuery(
            kind=PATROL_EVENT_KIND,
            subject_id=str(warden._warden_id),
            newest_first=True,
            limit=SEED_LOOKBACK,
        )
    )
    reviewed = next(
        (e.at for e in events if e.payload.get("outcome") != PatrolOutcome.SKIPPED.value), None
    )
    return (events[0].at if events else None), reviewed
