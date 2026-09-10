# ADR-0019: Queen kernel: autopilot-first, with stateless awake episodes

- Status: Accepted
- Date: 2026-09-09

## Context

The Queen is the Hive's only global view: every Warden, every task and every model call the shared
pool serves is visible to her, and nothing else in the Hive has that vantage point. That makes her
the natural place to put judgement -- but codingrules section 8.8 also requires the Hive to keep
working when every model provider is unreachable, and section 15 forbids anything below her from
widening its own capabilities or reach. A Queen shaped as a long-running conversation with a model
would fail both: she would be unusable the moment her own provider went down, and a persistent
session accumulating context over the Hive's whole lifetime is itself a growing attack surface and
a single point of failure no other bee has to carry. Roadmap step 3.18 additionally requires that a
task's acceptance criteria exist before any bee starts work on it, not be improvised afterwards by
whichever bee finishes first.

## Decision

The Queen is built like an operating-system kernel: a thin `waggle.loop.TickLoop` with a
prioritised inbox, exactly as codingrules section 8.8 describes. One tick drains every attached
Warden's own Waggle link into `InboxItem`s, orders them with her own Attendant (human messages
weighed heavily but not absolutely, a model-backed tie-breaker on `ModelSlot.ATTENDANT` only for an
exact tie), and dispatches each item through a deterministic table
(`hivemind.queen.autopilot.decide`, which never imports `hivemind.llm`) before ever considering a
model call. Only an item the table returns `NEEDS_JUDGEMENT` for runs one stateless awake episode
(`hivemind.queen.awake.decide_awake`): the episode assembles its whole prompt from durable state
(the Brood Chamber, her memory store, her human inbox) through `hivemind.memory.assemble`, decides
exactly one `QueenAction`, writes the decision back as an `EpisodeRecord`, and discards the
transcript -- nothing about one episode carries into the next. The Queen holds no `CellSession` and
no Comb Registry anywhere in her own state: her only levers over a Warden are the six
`hivemind.supervision.Intervention` values, sent over Waggle, and a task reaches `SUCCEEDED` only
after its own Warden -- never the Queen, never the bee that did the work -- has run its acceptance
checks. The planner (`hivemind.queen.planner.plan_goal`) is required to emit at least one
acceptance postcondition per subtask before a task graph is ever persisted, falling back to a
`JUDGE_RUBRIC` criterion only where nothing machine-checkable exists. Placement v0 is the Hive
Stand only: `hivemind.queen.placement.decide` picks the first attached Warden's own Cell, refusing
a task whose `TaskNeeds.isolation` is `REQUIRED` (no Cell can isolate until a Virtual Cell
provisioner exists) rather than pretending to place it.

## Consequences

Positive: the Hive keeps functioning end to end -- heartbeats recorded, ready tasks dispatched,
retries and rebinds carried out, Alarms escalated -- with every model provider down, because every
one of those moves is decided by the autopilot table first. Awake episodes cost nothing between
calls: no growing context to compact, checkpoint or leak across an episode boundary, and a fresh
Queen (after a crash, or after Requeening) needs no conversation to resume, only the durable state
every episode already reads from. The "no session, no registry" invariant is directly testable
(instance attributes and `QueenDeps`'s own fields can be introspected for a `CellSession`- or
`CombRegistry`-typed value) rather than resting on developer discipline alone. Negative: every
awake decision is a little more expensive per call than a persistent conversation would be, since
the whole relevant context is re-assembled and re-sent on every episode rather than incrementally
extended; the Queen also cannot rely on "what I already told the model" the way a chat-shaped
supervisor could, so `hivemind.memory.assemble`'s own packing quality now bounds how well-informed
one decision can be. Placement v0's Hive-Stand-only decision means any task whose `TaskNeeds` ask
for isolation simply cannot be dispatched yet -- expected and intentional for this phase, but a
real limitation until a Virtual Cell provisioner exists.

## Alternatives considered

- **A persistent Queen conversation.** Keeping one long-running chat session per Hive was rejected:
  it would make every tick depend on a live provider, contradicts the "awake episodes are
  stateless" rule the Wardens already follow (codingrules section 8.8), and turns the Queen's own
  context window into a silently growing resource nothing else in the Hive has to manage.
- **A Queen that spawns Workers directly.** Letting the Queen assign work straight to a Worker,
  bypassing the Warden, was rejected: it would give the only global-view process direct execution
  reach over every Cell in the Hive, the exact "one bug has a large blast radius" shape codingrules
  section 15 exists to prevent, and it would duplicate the Warden's own spawn, grant-attenuation
  and acceptance-checking logic a second time.
- **Placement by Cell kind.** Choosing a Cell by branching on `CellKind` (or by ranking every kind
  of Cell the Hive might one day have) was rejected in favour of a pure `TaskNeeds`-vs-capabilities
  decision: `scripts/check_no_kind_branches.py` already forbids a `cell.kind` branch outside
  `hivemind/queen/placement/`, `cell/` and the Undertaker, and a needs-based decision is the one
  shape that still works once a Virtual Cell provisioner exists without this module changing at
  all.
