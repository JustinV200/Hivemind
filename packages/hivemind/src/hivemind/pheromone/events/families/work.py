"""Define the event families of the work itself: Cells, tasks, Alarms and Workers.

Four of the Pheromone Trail's thirteen event families record the work and where it runs: a Cell's
lifecycle (`cell`), a Brood Chamber task's state machine (`task`), an Alarm's climb up the chain
(`alarm`) and a Worker's own state machine (`worker`). Each is a `PheromoneEvent` subclass
(`hivemind.pheromone.events.base`) fixing `FAMILY` (the prefix before the dot) and `KINDS` (every
kind string that family accepts); the base class's validators refuse anything outside them, so
the frozensets below and the vocabulary in this docstring are one normative list.

Vocabulary (family -> kind -> when it is recorded):
    cell: provisioned (a Virtual Cell backend created it); attested (Night Veil attestation ran,
        pass or fail per check); ready (its Warden's first heartbeat arrived); leased (a Real Cell
        lease opened); released (a Real Cell lease closed and the device restored); touched_
        outside_scratch (a lease wrote or read outside its scratch directory); left (release()
        wrote a Leavings ledger row for a persist=True restore record, roadmap step 5.0a);
        leaving_removed (`hive cells leavings remove` replayed and marked a Leaving row, roadmap
        step 5.0a); sting_cut (a human disconnected the Cell); overwintered (a Virtual Cell was
        paused dormant); destroyed (a Virtual Cell was torn down); purged (Night Veil teardown
        purge completed for the Cell); provisioning (a hivemind.hive.lifecycle.CellLifecycle.
        provision call began, roadmap step 5.6); granted (a Virtual Cell was handed to a task,
        GRANTED); virtual_released (a Virtual Cell's task ended, RELEASED, pending an
        overwinter-or-teardown decision -- distinct from `released` above, which is a Real Cell's
        own lease closing); resumed (an Overwintered Virtual Cell woke back to READY); destroying
        (a CellBackend.destroy call began, before `destroyed`); provision_failed (a CellLifecycle.
        provision call's own backend.provision raised); evicted (the Undertaker force-released a
        stale Real Cell lease or expired Overwintered Cell outside the normal `released`/
        `destroyed` path -- distinct from both, so an operator can tell a sweep-forced ending from
        a task-driven one); orphans_swept (one Undertaker sweep pass's rollup: how many Cells it
        evicted or destroyed, roadmap step 5.8); isolated (the Queen isolated the Cell: its
        Warden's grant revoked, its bees checkpointed and paused, a BLOCK Cell Wax written, a
        Virtual Cell's egress cut to its Waggle link; carries the reason, the GuardReport id and
        the trail ids that justified it, roadmap step 10.6a, ADR-0035); isolation_lifted (the human
        lifted an isolation, with step-up; the Queen never lifts one herself, 10.6a).
    task: submitted (BroodChamber.submit minted it); assigned (PENDING -> ASSIGNED); unassigned
        (ASSIGNED -> PENDING, Warden lost); started (ASSIGNED -> RUNNING); progressed (a progress
        report, no transition); blocked (RUNNING -> BLOCKED, a question was asked); answered
        (BLOCKED -> RUNNING, a question was answered); question_withdrawn (BLOCKED -> RUNNING, the
        question was withdrawn); paused (RUNNING -> PAUSED, Clustering); resumed (PAUSED ->
        RUNNING); succeeded (-> SUCCEEDED, acceptance checks passed); failed (-> FAILED); cancelled
        (-> CANCELLED).
    alarm: raised (a bee could not resolve an issue itself); handled (its supervisor resolved it
        without escalating); escalated (passed to the next level up); resolved (the issue is
        closed, at whichever level handled it, the human's own acknowledgement included).
    worker: spawned (a Warden started a sub-bee, roadmap step 3.19); started (SPAWNED -> RUNNING,
        its first TaskAssign arrived); handing_off (RUNNING/PAUSED -> HANDING_OFF, writing a
        Handoff before a reset, rebind, takeover or stop); paused (RUNNING -> PAUSED, TaskPause);
        resumed (PAUSED/HANDING_OFF -> RUNNING, TaskResume or a restart from a checkpoint); done
        (-> DONE, the role claimed the work done or the runtime stopped after a handoff); failed
        (-> FAILED, the role's own coroutine raised); killed (-> KILLED, TaskCancel or an
        Intervene(CANCEL) ended the attempt).

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.pheromone.events.
    families`. Constructed by every layer that moves a Cell, a task, an Alarm or a Worker; decoded
    by `hivemind.pheromone.events.families.codec`. Calls into `hivemind.pheromone.events.base`
    only.

Key invariants:
    - Every class's KINDS contains only strings whose family segment equals its own FAMILY.

See Also:
    - .claude/codingrules.md section 12 for the audit-log rules this vocabulary follows.
    - hivemind.pheromone.events.families.codec for EVENT_FAMILIES and the JSON codec.
"""

from __future__ import annotations

from typing import ClassVar

from hivemind.pheromone.events.base import PheromoneEvent

__all__ = ["AlarmEvent", "CellEvent", "TaskEvent", "WorkerEvent"]


class CellEvent(PheromoneEvent):
    """A Cell (Real or Virtual) lifecycle event; see the module docstring's `cell` entry."""

    FAMILY: ClassVar[str] = "cell"
    KINDS: ClassVar[frozenset[str]] = frozenset(
        {
            "cell.provisioned",
            "cell.attested",
            "cell.ready",
            "cell.leased",
            "cell.released",
            "cell.touched_outside_scratch",
            "cell.left",
            "cell.leaving_removed",
            "cell.sting_cut",
            "cell.overwintered",
            "cell.destroyed",
            "cell.purged",
            # Roadmap step 5.6 (hivemind.hive.lifecycle.CellLifecycle): the Virtual Cell
            # lifecycle's own edges not already covered above.
            "cell.provisioning",
            "cell.granted",
            "cell.virtual_released",
            "cell.resumed",
            "cell.destroying",
            "cell.provision_failed",
            # Phase 5 housekeeping: the Undertaker's own sweep actions, so they stop overloading
            # cell.destroyed, which is meant for a normal CellBackend.destroy call.
            "cell.evicted",
            "cell.orphans_swept",
            "cell.isolated",  # Roadmap step 10.6a: the Queen isolated the Cell (ADR-0035).
            "cell.isolation_lifted",  # Roadmap step 10.6a: the human lifted it, with step-up.
        }
    )


class TaskEvent(PheromoneEvent):
    """A Brood Chamber task-state-machine transition; see the module docstring's `task` entry."""

    FAMILY: ClassVar[str] = "task"
    KINDS: ClassVar[frozenset[str]] = frozenset(
        {
            "task.submitted",
            "task.assigned",
            "task.unassigned",
            "task.started",
            "task.progressed",
            "task.blocked",
            "task.answered",
            "task.question_withdrawn",
            "task.paused",
            "task.resumed",
            "task.succeeded",
            "task.failed",
            "task.cancelled",
        }
    )


class AlarmEvent(PheromoneEvent):
    """An Alarm's escalation-chain step; see the module docstring's `alarm` entry."""

    FAMILY: ClassVar[str] = "alarm"
    KINDS: ClassVar[frozenset[str]] = frozenset(
        {"alarm.raised", "alarm.handled", "alarm.escalated", "alarm.resolved"}
    )


class WorkerEvent(PheromoneEvent):
    """A Worker's state-machine transition; see the module docstring's `worker` entry.

    Roadmap step 3.15: recorded by `hivemind.workers.runtime.WorkerRuntime` for every
    `hivemind.workers.state.WorkerState` change it makes (Appendix C's "Worker" row), and by
    `hivemind.wardens.spawn` (roadmap step 3.19) for `worker.spawned`, the one transition that
    happens before a WorkerRuntime exists to record it itself.
    """

    FAMILY: ClassVar[str] = "worker"
    KINDS: ClassVar[frozenset[str]] = frozenset(
        {
            "worker.spawned",
            "worker.started",
            "worker.handing_off",
            "worker.paused",
            "worker.resumed",
            "worker.done",
            "worker.failed",
            "worker.killed",
        }
    )
