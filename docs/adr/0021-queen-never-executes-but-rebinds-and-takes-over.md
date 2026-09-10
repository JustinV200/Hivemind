# ADR-0021: Queen never executes but rebinds and takes over

- Status: Accepted
- Date: 2026-09-09

## Context

The Queen is the Hive's only global view (ADR-0019) and the top of its escalation chain
(ADR-0012). Every task fails somewhere eventually: a Drone crashes, a provider goes down, a
model turns out too weak for the objective. The roadmap's phase 3 list asks for a decision on
what the Queen does about that, because the tempting answer -- she does the work herself, or
hands it straight to a fresh Worker she controls -- would quietly undo the separations the rest
of the kernel is built on: a Warden owns every Cell and its lease (ADR-0010), acceptance is run
by the Warden and never by the bee that did the work (ADR-0018, codingrules section 8.12), and
an awake episode is assembled from durable state and discarded, never continued (codingrules
section 8.8). The six `Intervention` levers the protocol gives a supervisor include `REBIND`
and `TAKEOVER`, so the question is what those mean when the supervisor is the Queen.

## Decision

The Queen never executes. She holds no `CellSession`, runs no tool, makes no model call except
inside a stateless awake episode that ends in a single `QueenAction`, and assigns a task only to
a Warden -- `hivemind.queen.dispatcher` sends a `GrantIssued` then a `TaskAssign` to the Warden
that owns the placed Cell, never to a Worker. Recovery is by rebinding and by taking over, and
both keep the work on a Warden. When a sub-bee fails, its Warden's autopilot retries or respawns
it at `attempt + 1` from the sub-bee's last `Handoff` (roadmap 3.19): the fresh bee resumes the
task from durable state and the failed bee's transcript is gone, which is what "taking over"
means in this kernel -- a supervisor resumes the task through a new bee, not by continuing the
old one and not in its own process. When the escalation policy says `REBIND`, the Warden rebinds
within its grant's allowed bindings; when the grant has no room left, the Alarm escalates and
the Queen answers with `Intervene(REBIND)` naming the next binding in the slot's manifest
fallback chain (`hivemind.queen.autopilot`), never with a Worker of her own. When neither a
retry nor a rebind remains, the Alarm goes to the human inbox. `InterventionAction.TAKEOVER`
keeps its protocol meaning -- the supervisor resumes the task itself from the bee's Handoff --
and in this phase the supervisor that does so is always a Warden through a fresh sub-bee; a
Queen resuming a task in her own process is ruled out by this decision, not merely unbuilt.
Nothing on this path reads `cell.kind` or `provider.name`: the Warden's ceiling, the grant and
the policy table decide.

## Consequences

Positive: a failure never widens the Queen's privileges or gives her a session, so the
introspection test in ADR-0019 ("no session, no registry") stays true under every recovery
path; there is one recovery mechanism, `Handoff` -> respawn, shared by a crash, a stall, a
rebind and an operator's `TAKEOVER`, and it is exercised by the e2e scenarios for a killed
Drone and a twice-failing Drone (roadmap 3.22 b and c); and the Queen's awake episode can only
ever decide, which keeps her prompt small and her mistakes recoverable by the same chain.

Negative: every recovery costs a hop of Waggle latency and a fresh awake episode for the new
bee, so a flapping provider is slower to ride out than an in-place retry would be; a Warden must
exist whenever the Queen runs, which is why the Hive Stand's Warden stays in `WATCH` rather than
absent when its lease is refused (roadmap 3.19); and a task that needs a human's hands on the
Queen's machine has no path here at all -- it reaches the human inbox and waits.

## Alternatives considered

The Queen executing small tasks herself when no Warden is free: rejected because it
concentrates a session, a model call and acceptance in one bee, the exact combination
codingrules section 8.12 forbids, and because "small" is not a property the planner can promise.

The Queen assigning directly to a Worker she spawns: rejected because a Worker without a Warden
has no lease, no capability ceiling derived from one, no acceptance run by someone else and no
left-as-found guarantee (ADR-0010), so every safety property would have to be re-implemented on
the Queen.

Retrying in place by continuing the failed bee's context: rejected by codingrules section 8.8;
the transcript that led to the failure is the last thing a retry should inherit, and the
`Handoff` already carries what the next attempt needs.
