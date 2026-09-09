# ADR-0011: The Queen and every Warden run as a kernel: autopilot first, awake only when needed

- Status: Accepted
- Date: 2026-09-08

## Context

The Queen (the Hive's central orchestrator) and a Warden (the always-on supervisor of one Cell, a
unit of compute) both have to stay alive and responsive even when every LLM provider is
unreachable, and both have to make thousands of small decisions (a heartbeat arrived, a task
finished, a grant renewed) for every one that genuinely needs judgement. A chat-shaped design --
accumulate a conversation, call a model on every turn -- fails both requirements at once: it goes
silent the moment a provider is down, and it pays a model call for decisions a fixed rule already
answers. README's "Core concepts" section says the Queen is "run like a kernel... closer to an
operating system than to a chat session", with "an inbox, ordered by the Attendant", "autopilot
first" and "awake when it matters", and codingrules section 8.8 restates the same shape for a
Warden: "the same `Supervisor` protocol is used at every level." Codingrules section 4 requires
that any module under an `autopilot/` directory never import `hivemind.llm`, directly or
transitively, and `lint-imports` enforces it -- the constraint has to be structural, not a
convention someone might forget under deadline. Roadmap step 3.13 (supervision) is the first step
that has to fix the shared vocabulary (`Supervisor`, `Alarm`, `Intervention`, `EscalationPolicy`,
`Attendant`) both `hivemind.queen` and `hivemind.wardens` (later roadmap steps) build their own
autopilot and awake halves on top of, so the decision has to be recorded before either of those
land, not after.

## Decision

Every supervisor in the Hive (the Queen over Wardens, a Warden over its sub-bees) is built from the
same two-stage loop. First, **autopilot**: a deterministic dispatch table keyed on `(event kind,
current state)` that returns either a concrete action or the sentinel `NEEDS_JUDGEMENT`. Autopilot
code lives under a directory literally named `autopilot/` (`hivemind.queen.autopilot`,
`hivemind.wardens.autopilot`) and never imports `hivemind.llm`, a rule `lint-imports` enforces
mechanically rather than trusting review; this is what keeps the Hive alive when every model
provider is unreachable; simultaneously (codingrules section 4) `hivemind.supervision`
itself -- the package both autopilot packages import for `Supervisor`, `Alarm`, `Intervention` and
`EscalationPolicy` -- carries the same restriction, because a transitive path through it into
`hivemind.llm` would defeat the guarantee just as completely as a direct import. Second, **awake**:
only when autopilot returns `NEEDS_JUDGEMENT` does an awake episode run, and every awake episode is
stateless -- its prompt is assembled fresh each time by `memory.assemble` (a later roadmap step)
from durable state (hot state, pins, the triggering event) plus the event itself, makes exactly one
decision, writes that decision back to durable state, and discards the assembled prompt and the
model's response afterwards. No module anywhere in this shape keeps a growing conversation across
episodes; `scripts/check_no_transcripts.py` polices the concrete symptom (a `messages`/`history`
attribute that accumulates outside `hivemind.memory`) as a mechanical backstop for the same rule.
Continuity across episodes lives in hot state (an open Alarm, a pending Question, a Handoff) or is
simply re-derived from the stores each time, never carried in a Python object between calls.

## Consequences

Positive: the Hive keeps functioning -- heartbeats answered, tasks progressed within an existing
grant, known Alarms handled by policy -- for as long as every provider the Hive depends on is down,
because the path that keeps the lights on structurally cannot reach a model. Most events never pay
for a model call at all, which keeps latency and cost down for the overwhelming majority of
traffic (a heartbeat, a routine task result) that a fixed rule already answers correctly. Because an
awake episode never accumulates state, a bee's context cannot leak across unrelated decisions, a
prompt-injection risk chat-shaped designs are structurally exposed to; every decision is auditable
independently, since its whole input was reconstructed from durable state that is itself on the
record. Testing autopilot needs no model at all -- a dispatch table over `(event kind, state)` is a
pure function tested with plain data -- and testing an awake episode needs only a scripted
`FakeLLMProvider` response, not a multi-turn conversation fixture.

Negative: a decision that would benefit from a few turns of the model's own back-and-forth (the
kind a chat naturally accumulates) instead has to be re-derived from scratch each episode, which
can mean the model re-reasons about the same durable facts more than a stateful design would;
`memory.assemble`'s budget and relevance logic (a later roadmap step) exists specifically to make
that re-derivation cheap enough to be worth it. A dispatch table has to be kept exhaustive as new
event kinds and states are added -- an event nobody wrote a rule for either needs a wildcard
default or silently falls through -- which is an ongoing maintenance cost a chat-shaped design,
where "the model figures it out," would not carry in the same visible way. Splitting every decision
into two stages (a table lookup, then possibly a model call) is one more concept a new contributor
has to learn before touching the Queen's or a Warden's tick, compared to a single "call the model"
entry point.

## Alternatives considered

One long-running conversation per bee, compacted periodically: the natural design if a chat
session were the model, but it fails the moment a provider is unreachable (nothing in a pure
conversation can proceed without a model call), it accumulates exactly the growing-transcript risk
codingrules section 8.8 and `scripts/check_no_transcripts.py` exist to forbid, and it makes an
audit of "why did the Queen do X" require replaying a whole conversation rather than reading one
assembled prompt and one decision.

Always calling a model, but with a cheap/fast model for routine events: still fails the "stay alive
with every provider down" requirement, still costs a network round trip and nonzero spend for
traffic that is genuinely deterministic (a heartbeat, a routine task result), and blurs the line
between "this decision is a fixed rule" and "this decision needs judgement" in a way that makes the
Hive's behaviour harder to reason about and to test without a model in the loop.

A single dispatch table with no awake escape hatch at all: would keep the Hive fully deterministic
and provider-independent, but it cannot handle the genuinely judgement-shaped cases the roadmap
requires (deciding a rebind is worth it, deciding a question should reach the human) without either
guessing via a fixed rule that is wrong often enough to matter, or growing the table into an
unmaintainable pile of special cases that is really just a badly-disguised model call.
