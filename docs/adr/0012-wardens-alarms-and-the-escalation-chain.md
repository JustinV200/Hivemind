# ADR-0012: Wardens, Alarms and the escalation chain: one Warden per Cell, human last, policy as data

- Status: Accepted
- Date: 2026-09-08

## Context

Every Cell (Real or Virtual, a unit of compute) needs a supervisor that can watch its sub-bees,
handle their failures locally when it can, and hand off what it cannot -- but nothing below the
Queen (the Hive's only globally-scoped component) should be able to reach the human directly, and
no failure should silently vanish or be handled twice as it climbs. README's "Core concepts"
section 2 fixes the tree as "human -> Queen -> Wardens -> sub-bees" and says a Warden "handles
[sub-bees'] Alarms by a policy table (retry, respawn, rebind to a stronger model within its grant)
and escalates what it can't resolve to the Queen. A Warden never addresses the human." Codingrules
section 8.8 adds the mechanics: "an issue a bee cannot resolve becomes an `Alarm` (id, kind,
severity, origin, attempts, context reference) sent to its supervisor... Each level's
`EscalationPolicy` is data (TOML) mapping `(kind, attempts)` to retry, respawn, rebind, takeover or
escalate... Every hop is a trail event carrying the same alarm id so no level handles it twice."
Roadmap step 3.13 is the step that has to fix `Alarm`'s shape, its state machine and the policy
format once, before either `hivemind.wardens` (step 3.19) or `hivemind.queen` (step 3.20) can be
built against it, because both are `Supervisor` implementations over the same escalation chain.

## Decision

Every Cell gets exactly one Warden (codingrules section 6.1's "Warden" row: "One per Cell.
Supervises sub-bees; never provisions Cells."), and every Warden and the Queen alike implement the
one `Supervisor` protocol (`hivemind.supervision.supervisor.Supervisor`): list children, read
telemetry, inspect a compacted view, intervene. An issue a bee cannot resolve on its own is raised
as an `Alarm` (`hivemind.supervision.alarm.Alarm`): a typed `kind` (the closed
`hivemind.supervision.alarm.AlarmKind` set the policy keys on), a `severity`, the `origin` bee, an
`attempts` count, typed `context` references (task, Cell, bee, trail event, Handoff -- carried
directly from waggle's own `AlarmContext`, never a free-text blob), a bounded `detail` string, and
a `state` that moves along exactly one transition table
(`hivemind.supervision.alarm.TRANSITIONS`: `RAISED -> HANDLING -> RESOLVED`;
`HANDLING -> ESCALATED -> HANDLING` at the next level). The same `alarm_id` travels unchanged at
every hop -- forwarding an Alarm never mints a new id -- so a trail event at each level names the
same Alarm and no level can process it twice without that being visible on the record. Each
supervisor's response to a HANDLING Alarm is decided by its own `EscalationPolicy`
(`hivemind.supervision.policy`), loaded from a TOML file (`docs/supervision/default-policy.toml`
ships the Hive's default) rather than compiled into code: a `PolicyRule` names an `AlarmKind` (or a
wildcard) and a minimum attempt count, mapped to a `PolicyAction` (`RETRY`, `RESPAWN`, `REBIND`,
`TAKEOVER`, `ESCALATE`, `CANCEL`); `decide` is a pure function so the same `(policy, alarm)` pair
always yields the same action, and a Warden's playbook can be retuned by editing data, never by
shipping new code. `ESCALATE` is the only action that ever moves an Alarm past the current
supervisor, and it always moves it exactly one level up the fixed chain
sub-bee -> Warden -> Queen -> human; a Warden's `EscalationPolicy` therefore never has a legal
action that reaches the human, because the human is not a `Supervisor.intervene` target a Warden
can address at all -- only the Queen decides when an Alarm or a Question reaches the human's inbox
(`queen/human_inbox.py`, a later roadmap step).

## Consequences

Positive: because the alarm id never changes across hops, the Pheromone Trail (the Hive's audit
log) can always answer "who has seen this Alarm and what did each of them do about it" with a
single query keyed on that id, with no risk of two levels silently duplicating the same fix.
Putting the escalation policy in TOML rather than code means an operator (or a later phase's
tuning pass) can change "retry twice before rebinding" into "retry once" without a deployment,
and `hivemind.supervision.policy.decide`'s purity means the whole policy can be exhaustively
table-tested with no Warden, no Cell and no model in the loop at all. Fixing the chain's shape
(sub-bee -> Warden -> Queen -> human, human always last) once, in a package neither `hivemind.
wardens` nor `hivemind.queen` needs to reinvent, means both of those roadmap steps inherit the
"a Warden never addresses the human" guarantee for free rather than having to each enforce it
themselves. Typed `context` references (rather than a free-text explanation) keep an Alarm cheap
to carry up the tree and cheap to render in the Observation Hive, and bounding `detail`'s length
keeps codingrules section 8.8's promise that "full transcripts never travel up the tree" true by
construction rather than by discipline.

Negative: a fixed vocabulary of `AlarmKind` members means a genuinely novel failure mode has
nowhere precise to go until a minor version names it -- `OTHER` exists as an escape hatch, but
every `OTHER` Alarm falls back to whatever the wildcard or default policy row says, which is
necessarily a blunter response than a purpose-built rule would give. A policy file's `decide`
function is only as good as its author's foresight: a rule with the wrong `min_attempts` threshold
fails silently (it simply matches at the wrong point) rather than raising anything a test would
catch unless that specific `(kind, attempts)` pair is exercised. Requiring every escalation to pass
through exactly one level at a time (never skipping a Warden to reach the Queen directly, never
skipping the Queen to reach the human) adds one hop of latency to a genuinely urgent Alarm compared
to a design that let a sufficiently severe Alarm jump straight to the top; the tree's supervision
shape trades that latency for the auditability of every hop being visible on the trail.

## Alternatives considered

Let a sufficiently severe Alarm bypass the chain and reach the Queen (or the human) directly: would
cut latency for the worst cases, but breaks "one supervisor's own state accounts for the Alarms it
is currently handling" -- a Warden that never saw an Alarm skip past it cannot know its own sub-bee
is unresolved, and codingrules section 8.8's "no level handles it twice" guarantee would need a
second mechanism to hold once hops can be skipped.

A single global escalation policy shared by every Warden and the Queen: simpler to reason about,
but ignores that a Warden's own Cell may have local constraints (a slower host, a smaller model
roster) a global policy cannot account for, and it removes the per-level tunability codingrules
section 8.8 explicitly wants ("each level's EscalationPolicy is data").

Encode the escalation policy as Python (a function per kind, or an `if`/`elif` chain) instead of
TOML: avoids the TOML-loading and validation code this ADR's decision requires, but turns "the
operator changes a retry threshold" into a code change and a deployment, which is exactly the
coupling codingrules section 13's "policy as data" principle exists to avoid.
