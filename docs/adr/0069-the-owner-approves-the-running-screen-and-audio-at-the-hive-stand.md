# ADR-0069: The owner approves the running screen and audio for each task, at the Hive Stand

- Status: Proposed
- Date: 2026-10-29
- Amends: ADR-0031 (the operator's opt-in alone let a bee drive the running display)

## Context

ADR-0031 lets a Worker drive the display already running on a Real Cell (`exoskeleton:real_display`)
when that Cell's operator opted in (`exoskeleton_real_display = true` in its manifest, reported as
`CellCapabilities.real_display_allowed`) and its access level is `FULL`. Audio
(`exoskeleton:audio`) needs only a sound server and `FULL`. Both reach the Worker from the task's
needs, which the planner writes, and from the goal's capability set, which the planner's goal
carries. So once a Cell has opted in, any task the planner shapes as a desktop task is handed the
operator's own screen, and any audio task the microphone and speakers, with no one asked. These
are the two peripherals that watch the owner. An opt-in made once in a manifest is consent to the
possibility, not to a particular task.

The Hive already has a way to take a human decision that a bee must never take for itself:
loopback-only rows on the Hive Entrance (ADR-0041), which since ADR-0068 also need the per-boot
stand key that only the Hive Stand's console and its printed browser link hold. Enrolment's
approval is the model: listed for a steward, decided only at the Hive Stand.

## Decision

**A Worker holds `exoskeleton:real_display` or `exoskeleton:audio` only for a task the owner
approved on that Cell from the Hive Stand; the manifest opt-in stays a prerequisite.**

- *The dispatcher holds the task.* Once placement has chosen a Cell, and before a grant is sized
  or anything is sent, `hivemind.queen.peripherals.gate.hold_for_owner` works out which of the two
  scopes the task's Worker would be offered there: the running display for a desktop need on a
  Cell that has one, whose operator opted in and whose level allows it; audio for an audio need
  the level allows. If either applies, it files one watch request (`WatchRequest`: the scopes, the
  Cell and its name, the task and its title, the Warden and the Worker role that would hold them)
  and leaves the task `PENDING`, recording `queen.decided` with `reason=owner_approval_pending`.
  A task that asks for neither, the lease's own virtual display (`exoskeleton:display`) and the
  browser included, is never held.
- *The owner answers at the Hive Stand.* `GET /v1/entrance/watch` lists the waiting requests for a
  steward (either listener, C2: it shows the task title). `POST
  /v1/entrance/watch/{request_id}/approve` and `/deny` are loopback-only rows needing
  `entrance:steward`, so they are never mounted on the remote listener (a remote device, the one
  that would be watched included, gets `404`) and are refused with a bare `403` without the stand
  key (ADR-0068). They reach the Queen through her door (`QueenDoor.answer_watch_request`), which
  records `queen.decided` (`reason=owner_approval_answered`) and wakes her. The Observation Hive
  shows the list on the Enrol screen, which is drawn on the Hive Stand only, with Allow and Refuse.
- *An answer is final for that task on that Cell.* An approved request lets the task through; a
  denied one, or one unanswered for fifteen minutes (`WATCH_REQUEST_TTL`), cancels the task with
  the reason (the chamber's `PENDING -> CANCELLED` edge). A task placed on another Cell needs its
  own answer. Requests live in the Queen's `DispatchBook` in memory: a restarted Queen asks again
  rather than trust an answer she did not hear, and a request is forgotten once its task ends
  (to keep the book bounded, only denied or expired requests are ever dropped early: an approval
  stays while its task lives). A retry or a resume skips placement, so before either the dispatcher checks the book:
  a task with no approval on record for its Cell (a Queen that restarted or took the Hive in a
  Supersedure, a Clustering resume) is moved back to `PENDING` (`chamber.unassign`, a new attempt,
  its grants released and a resume's Handoff kept for the fresh placement), and that placement
  asks the owner again. It is never sent with the scopes silently stripped.
- *The approval travels with the assignment, and the Warden enforces it.* `TaskAssign` gains
  `owner_approved` (Waggle 1.17, additive): which of the two scopes the owner approved for this
  task on this Cell; nothing else validates there. The Warden runs `owner_approved_only` on its
  Worker's slice, which drops either scope unless it is named, whatever the Warden's own set, the
  role default or the goal's set allow; a wildcard `exoskeleton:*` keeps the virtual display and
  the browser. So a planner-written goal set, or a task need, can never grant either alone, and
  nothing a bee or Honey can write approves anything: the only writer of an approval is the
  Entrance row above.

## Consequences

Positive: the operator's screen and the sound server are used only for a task the owner saw and
allowed, from the one place the Hive already trusts for such decisions, and never from a remote
device or the machine that would be watched. The check is enforced twice: the Queen never assigns
the task unapproved, and the Warden never hands the scope over without the approval on the signed
assignment. Virtual-display and browser tasks are unchanged.

Negative: a desktop task on a Cell with the opt-in waits for the owner even where a virtual display
would also have done, because attach prefers the running display when the scope is held. Nothing
notifies the owner yet beyond the trail row and the Enrol screen; a task left unanswered is
cancelled after fifteen minutes. A Warden older than Waggle 1.17 would still grant the scopes from
the opt-in alone, but it refuses a 1.17 assignment, whose new field it does not know, and the Queen
never assigns such a task unapproved in any case. `hive run` without `hive
serve` has no Hive Stand browser to answer from, so such a task is refused when its request
expires.

## Alternatives considered

Approving once per Cell or per goal: one yes would cover tasks the owner never saw. Asking from
any steward device after step-up, like the steward approval route: the device that would be
watched could approve itself. A durable SQLite table for requests: an answer outliving the Queen
that heard it is not needed for a fifteen-minute wait, and re-asking after a restart is safer.
Stripping the scopes from `TaskAssign.capabilities` instead of a new field: a local goal carries no
ceiling (`None`), so there is nothing to strip from.
