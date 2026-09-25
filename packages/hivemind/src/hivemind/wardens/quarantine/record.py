"""Define QuarantineRecord: what a Warden holds about one quarantined task until it is let out.

After the one quarantine code path (`hivemind.wardens.quarantine.path`, roadmap step 10.6c) has
run, the Warden (the supervisor of one Cell) keeps one record per quarantined task in its own
`_quarantined` table: which bee was quarantined, from which suspect episode, the checkpoint
(a Handoff) it wrote before cutting anything, and the `warden.intervened` event every
`memory.tainted` row names as its cause. The record is the Warden's half of the task's `PAUSED`
state (the Brood Chamber, the Queen's task store, holds the other half): while it stands, no
assignment for the task spawns unless it resumes from that checkpoint once a judge has cleared it
(`hivemind.wardens.quarantine.gate`), the only way out ADR-0043 allows.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    quarantine sub-package. Built by `hivemind.wardens.quarantine.path`, kept on
    `hivemind.wardens.warden.Warden._quarantined`, read by the gate and by the reports the path
    sends the Queen. Calls into waggle only.

Key invariants:
    - A record exists only for a task whose quarantine ran to the end: checkpointed, stopped,
      revoked, recorded and tainted.
    - It is dropped only by the gate, when a respawn resumes from its cleared checkpoint.

See Also:
    - hivemind.wardens.quarantine.path for the path that writes it.
    - hivemind.wardens.quarantine.gate for the respawn that lifts it.
"""

from __future__ import annotations

from dataclasses import dataclass

from waggle.ids import EventId, TaskId, WorkerId
from waggle.messages import HandoffRef
from waggle.messages.labels import HoneyClearance

__all__ = ["QuarantineRecord"]


@dataclass(frozen=True, slots=True)
class QuarantineRecord:
    """One quarantined task, as its Warden holds it until a judge-cleared respawn lets it out."""

    task_id: TaskId  # The task held paused.
    bee: WorkerId  # The sub-bee that was quarantined.
    suspect_episode_id: EventId  # Its memory from this episode on is tainted.
    checkpoint: HandoffRef  # The Handoff written before anything was cut: the only way out.
    intervened_event_id: EventId  # The `warden.intervened` event, every taint's recorded cause.
    tainted_count: int  # How many memory items the quarantine newly labelled.
    clearance: HoneyClearance  # The task's own label, for every report the Warden sends about it.
