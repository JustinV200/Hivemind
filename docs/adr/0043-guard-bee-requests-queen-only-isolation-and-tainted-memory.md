# ADR-0043: The Guard Bee watches and requests; only the Queen isolates; tainted memory never reaches a prompt

- Status: Accepted
- Date: 2026-09-24

## Context

Phase 10 gives the Hive a security watcher, the Guard Bee (a Worker specialised in security and
monitoring), and a response ladder: raise an audit rate, reduce the Entrance, quarantine a bee,
isolate a Cell, Sting Cut (roadmap 13.4a). Three things are hard to reverse. Who may pull which
lever: a watcher that can also act on a Cell becomes the most valuable thing to subvert, and a
false positive that isolates the wrong Cell costs a task. How the Queen hears about it: a Guard
report must not wait behind routine traffic, must not be something a prompt injection can forge,
and must not let a flood of weak reports take over her attention. And what happens to memory
written while a bee was compromised: an injected instruction that survives into a Handoff, a
checkpoint or Nectar comes back in every later prompt that assembles it.

## Decision

**The Guard Bee watches from the Queen's process and never touches a Cell.** It runs on the Hive
Stand, driven by a Queen tick exactly as the House Bee is, so no Cell action can take it down, and
it follows the central trail. Its role set is fixed and small (it reads the trail in-process, which
no capability gates, and calls `llm:judge`), and its model calls are charged to the Royal Reserve
beside the Queen's and the House Bee's. Its rules are deterministic and are data: denial rates,
out-of-scratch touches, over-grant spend, unexpected network attempts, Capping rejection and
rollback rates, failed sampled audits, `guard.injection_suspected` events and their correlation
with a denial in the same episode, node integrity failures, and Entrance events (failure bursts,
lockouts after a valid device proof, invite-route abuse, travel-lock triggers). Its awake
episodes, for patterns the rules mark as needing judgement, run on `ModelSlot.JUDGE`, which the
manifest may pin to a different provider from `WORKER` so blind spots do not correlate; they only
file reports, and it never calls a model on the hot path.

**It acts alone only to narrow the whole Hive.** By autopilot rule it may raise a Capping tier's
sampled-audit rate, and it may order the Entrance Reducer (ADR-0041) by recording a
`guard.reduce_ordered` trail event that the Entrance, which follows the trail, carries out; a
Worker cannot import the Entrance, and does not need to. Narrowing is always safe. Everything
aimed at one Cell or one bee (isolate, quarantine, Sting Cut) is a **request**: a `GuardReport`
(the rule that fired, the trail event ids, the affected Cell, bees and grants, the recommended
action and a confidence tier) that enters the Queen's inbox as `InboxKind.GUARD_REQUEST`. The
Guard files a request only at or above `[guard] request_confidence`, coalesces repeats per rule
and Cell, and is capped per hour. `WeightTable.queen_default()` gives a Guard request a fixed
weight (100) above every `ALARM` and human message and no task-link or latency term, with the
Guard principal's multiplier on top, so only age orders two of them; a Warden's table never sees
one. The Guard Bee can never widen anything. Every report is a `guard.alert` trail event and a
`C2` deposit on the Honey browser's path (Nectar once phase 7's intake exists); a report the Queen
acts on, or one at `CRITICAL`, reaches the human as an Alarm pushed to every device. `AlarmKind`
gains `SECURITY` (on the wire and in the Hive's mirror) and the wire's `InterventionAction` gains
`QUARANTINE`, whose `Intervene` carries the episode id from which memory is suspect: one Waggle
minor version bump (1.9). The Hive-side `PolicyAction` of the escalation tables gains `ISOLATE`
and `QUARANTINE`, which no wire enum mirrors, so every table that maps it gains rows in the same
change, and a Warden's policy refuses to load an `ISOLATE` row.

**Only the Queen isolates a Cell.** `queen/isolation.py` is the single code path: revoke the
Cell's Warden grant; checkpoint and pause every bee on the Cell, waiting for their
acknowledgements or a bounded timeout; write a `BLOCK` Cell Wax so nothing is placed there; set a
Virtual Cell's egress to `none` except its Waggle link, which checkpointing, pausing and forensics
need; and keep the lease and its scratch intact. It is recorded as `cell.isolated` with the reason,
the `GuardReport` id and the trail ids that justified it, and pushed to the human. The Queen decides
by autopilot rule for the dire patterns listed in `[guard]` and by awake episode with the report
attached otherwise; when her awake mode is unavailable her autopilot fallback on a
`GUARD_REQUEST` is to isolate, since isolation only removes access. Lifting isolation needs the
human with step-up; the Queen never lifts it on her own. The Hive Stand's own lease is isolated
only by the human: on a dire pattern there her rule quarantines the implicated bee, holds new
placements for that goal on the Hive Stand and raises a `CRITICAL` Alarm with the report.

**Quarantine is one intervention.** `Quarantine` joins the `Intervention` union and
`InterventionAction` on the wire, carrying the episode id from which the bee's memory is suspect.
One code path in `wardens/` does it all: checkpoint, cancel, kill the tracked process, revoke the
bee's slice of the grant, taint every checkpoint, Handoff and Nectar from that episode on, move
the task to `PAUSED`, record `warden.intervened` and `memory.tainted`. The Queen orders it on a
Guard request; a Warden may apply it to its own sub-bee by its own policy row (it may already
cancel one), and the Queen is told either way. The only way out is a respawn from a Handoff the
judge has cleared. Decoding an intervention the Hive does not know is an error, never a silent
cancel.

**Taint is one label with three setters and one clearer.** `tainted` is a marker on checkpoints,
Handoffs, episode records, Nectar and Honey items, carrying the reason and the trail event that
set it. It is set by isolation (from the report's first cited event onward), by quarantine, or by
the Queen on a Guard report about a Honey item, and by nothing else; a test asserts no other path
writes it. `memory.assemble` (the resumed Handoff included, which today bypasses the clearance
filter), Honey retrieval and every Handoff loader refuse a tainted item outright, whatever its
relevance score. Only a judge verdict on the taint rubric, with no shared context, clears it
(`memory.taint_cleared`); a House Bee duty re-ripens tainted Nectar with the flagged span stripped
into a new item that starts tainted and goes to the judge, and the old item is retired, never
edited.

**The injection signal comes from one deterministic scanner.** `guard/scanner.py` runs, with no
model, wherever outside text enters a prompt: tool results in the Worker runtime, session output,
Honey hits at assembly, Nectar intake, Landing Board messages. Its patterns are data shipped in
`guard/defaults/untrusted-content.toml` and read through `importlib.resources` (imperatives
addressed to the model, role and identity overrides, secret paths beside exfiltration verbs,
encoded blobs over a size, tool-call-shaped text, hosts outside the task's targets), each with a
weight, and `[guard] untrusted_content` sets per-tier thresholds for labelling harder or dropping;
`docs/guard/` explains them. Input is bounded before matching, so a hostile document cannot make
the scanner itself the slow path. It emits `guard.injection_suspected` with the source, the
consuming bee and a keyed hash of the content (an HMAC under a Hive-local key, so the hash cannot
be used to confirm a guess), never the text, because the trail carries no prompt text. A flag
alone never stops a bee; only the Guard Bee's correlation rule escalates. The invariant, tested
with seeded payloads through a tool result (and through Honey once phase 7 lands): an injected
instruction can at most make a bee ask; it never widens a grant, never reaches an outside-scratch
write uncapped, and always leaves a `guard.*` event.

## Consequences

Positive: subverting the Guard Bee buys an attacker a request, not an action; a forged report is
still only a request the Queen weighs; isolation and quarantine each have one implementation to
test; memory written under compromise cannot come back through a later prompt; the scanner's
behaviour changes only by a visible diff to one data file, which the chaos tests (13.6) reuse.

Negative: the Queen's inbox gains a kind that outranks the human, which is deliberate but means a
noisy Guard rule could crowd her attention; confidence thresholds, coalescing and an hourly cap are
data for that reason. Isolation keeps leases, scratch and the Waggle link alive, so an isolated Cell
holds resources until the human lifts or destroys it. Deterministic patterns miss novel
injections; the design accepts that because the invariant (a flag can make a bee ask, never widen)
holds whether or not the scanner fires. Until phase 7's intake and retrieval exist, the Honey half
of taint and the Nectar deposit of reports are wired to seams, not stores.

## Alternatives considered

A Guard Bee that isolates on its own: the watcher becomes the lever an attacker wants, and one
false positive stops a Cell without a second look. Guard reports as ordinary Alarms: they would
queue behind routine Alarms and human chat. A Guard Bee that calls the Entrance Reducer directly:
a Worker importing the Entrance breaks the layer table, and a trail event is already the audit
record the reduction needs. A model-based injection classifier: expensive on every tool result,
itself injectable, and its misses are no better characterised than a pattern file's. Deleting
suspect memory instead of tainting it: forensics lose the evidence, and a false positive destroys
legitimate work that a judge could have cleared. Cutting every network path of an isolated Cell:
the Queen could no longer checkpoint, pause or examine it.
