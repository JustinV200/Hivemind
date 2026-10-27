# ADR-0061: The Nurse Bee reviews an attempt in a fresh context

- Status: Proposed
- Date: 2026-10-27

## Context

A Warden accepts an attempt once its deterministic acceptance checks pass (`wardens.acceptance`,
called from `wardens/ticks/results.py::handle_accept`). Those checks see only what a machine can
check: a file exists, a command exits zero, a page shows a text. Whether the work meets its
objective is never asked. `PostconditionKind.JUDGE_RUBRIC`, the planner's stand-in for "nothing
here is machine-checkable", is "unsupported in v0", so such a criterion passes nothing and fails
nothing. And the Worker that wrote the work is the only model that ever judged it, in the same
context that produced it, with every assumption it made still in view.

The operator decided (2026-10-26) to add a runtime reviewer, the Nurse Bee: a model with no share
in the Worker's context that reads the evidence and says PASS or what to fix.

## Decision

**Where it runs.** In `handle_accept`, after `run_acceptance` passes and before the Warden sends
its SUCCEEDED result. Deterministic acceptance always runs first; the Nurse only ever sees an
attempt the machine checks already passed, and it is what finally makes `JUDGE_RUBRIC` checkable.
Every role is reviewed except those in `[nurse] skip_roles` (default `UNDERTAKER` and
`HOUSE_BEE`); SCOUT tasks are reviewed.

**A fresh context, built by the Warden alone.** The Warden assembles a `NurseRequest` from what it
holds, never from what the Worker says about itself: the objective (capped), the acceptance
criteria and their deterministic outcomes, the actions of the attempt's VERIFIED Capping proposals
(filtered to that Worker, with no proposer id), `claim.artifacts`, and bounded reads of the paths
those actions touched, through the Warden's own session. Every file read passes the Guard's
untrusted-content scanner and reaches the model delimited and labelled as RETRIEVED, untrusted
content. The Worker's transcript, hot state, Handoff, `Proposal.reason` and `claim.summary` are
never included: each is the Worker's own account, which is what a fresh reviewer must not inherit.
v1 is one structured call over that evidence; a Nurse that uses tools comes later.

**At least as strong as the Worker.** The Nurse runs on `ModelSlot.JUDGE`, filtered to bindings
whose `effective_grade` is at or above the Worker's grade, preferring a different provider. The
Worker's grade is the highest over its bindings and over the models its `llm.call` events actually
reached, so a spill to a stronger model is caught. When the Worker already ran on the top model,
the identical model in a fresh context counts as equal. When no binding is eligible, the attempt
is accepted with a "not nursed" flag: a `capping.nurse_unavailable` trail event and a line in the
inbox digest, never an escalation.

**Fix forward, then a human.** A `CHANGES_REQUESTED` verdict fails the attempt (the Warden sends
FAILED with the review); a Queen autopilot table rule, reading wire fields only, retries it on a
fresh Worker on the same Cell with the findings and the Cell's state as it stands. A Virtual Cell
keeps its changes; nothing is rolled back. The Queen counts rounds in a Brood Chamber column,
`nurse_rounds`; at `[nurse] max_rounds` (default 2) she escalates to the human with
`AlarmKind.NURSE_UNRESOLVED` instead.

**Cost.** The Nurse's call goes through `fanner.lane(task.tempo)` after a budget pre-check, and its
spend is added to the attempt's `TaskResult.spend`.

**Configuration.** A `[nurse]` manifest section: `enabled = true`, `max_rounds = 2`,
`skip_roles = ["UNDERTAKER", "HOUSE_BEE"]`, `max_evidence_chars`. There is no per-tempo opt-out.

**Layering.** The `NurseReviewer` protocol and the request model live in
`supervision/capping/checks/nurse.py`, which imports nothing from `hivemind.llm`; the model-backed
implementation lives in `wardens/nurse.py`, beside a `FakeNurseReviewer`. The Queen's rule reads
only the wire fields below.

**Wire (Waggle 1.15, additive).** `waggle.messages.task.review` defines three frozen value models
that forbid extras:

- `NurseFinding`: `criterion_index`, `path`, `line`, `problem` (at most 300 characters) and `fix`
  (at most 300). It must be anchored.
- `NurseVerdict`: `outcome` (`PASS` or `CHANGES_REQUESTED`), `findings` (at most 8), `rubric_id`
  and `notes` (at most 1000). It is `PASS` exactly when it has no findings.
- `NurseReview`: `status` (`REVIEWED`, `UNAVAILABLE` or `ERROR`), `round`, `verdict`,
  `nurse_grade` and `worker_grade`.

`TaskResult.review` carries a `NurseReview`, and `TaskAssign.review_findings` carries the findings
to the fixing Worker. `AlarmKind.NURSE_UNRESOLVED` joins the closed set, with a shipped policy row
that escalates from the first attempt.

**Trail.** Two capping-family kinds, `capping.nurse_reviewed` and `capping.nurse_unavailable`,
about the task and carrying ids, enums and counts only (round, status, outcome, finding count,
grades), never a finding's words. Neither carries a `tier` key, so the Night Veil teardown's
`tier_counts` never counts one; neither is in the skeleton, so both die with a Night Veil Cell.
Both are classified as observations in the Guard's event catalogue.

### Deviations from the design pass, made in step 1 (the schema)

- **`TaskAssign.review_round` is added** beside `review_findings` (0 to 8, default 0). The design
  puts `round` on `NurseReview` but gave the Warden no way to know it: findings alone cannot tell
  round 2 from round 3. The Queen owns the count (`nurse_rounds`) and sends it down; the Warden
  stamps its review one higher. Findings are refused when `review_round` is 0.
- **`NurseReview.verdict` is named.** The design lists status, round and the two grades; the
  verdict itself must travel too, and is set exactly when `status` is `REVIEWED`.
- **The anchors are tightened.** A finding needs a `criterion_index` or a `path`; a `line` is
  allowed only with a `path`, because a line with no file points nowhere.
- **The wire refuses three impossible states,** so a later bug fails loudly instead of slipping
  through:
  - a review on a Worker's claim (a Worker never reviews itself);
  - a `CHANGES_REQUESTED` review on any outcome but `FAILED` (a rejected attempt never reaches
    SUCCEEDED);
  - a `nurse_grade` below `worker_grade`.
  `nurse_grade` is also None exactly when `UNAVAILABLE`, because no Nurse was chosen.
- **`worker_grade` is required.** The Warden always knows the Worker's bindings, so an unknown
  grade would be a bug, not a state.
- **Bounds.** `round` is at most 8 on the wire (`MAX_NURSE_ROUND`), a ceiling well above the
  default `max_rounds` that stops a corrupt count, not a policy. `rubric_id` is 1 to 64
  characters, and `criterion_index` is 0 to 31, matching `MAX_ACCEPTANCE_ITEMS`.

## Consequences

- An attempt reaches SUCCEEDED only after a model that did not write it has read the evidence,
  wherever a Nurse is eligible; where none is, the trail and the digest say so.
- `JUDGE_RUBRIC` criteria finally mean something.
- Every reviewed attempt costs one more structured call, charged to its own spend under the same
  budget, and adds its latency before the result goes up.
- A rejected attempt costs a fresh Worker, not a rollback, so the Cell carries both attempts'
  changes; the findings tell the next Worker what to fix, not what to undo.
- A Nurse's prose is untrusted model text written from untrusted evidence. It travels to the next
  Worker only delimited as such, and never onto the trail.
- A peer below minor 15 still validates every message that leaves the new fields unset, and a
  sender uses none of them toward it.

## Alternatives considered

- **Let the Worker review itself before claiming.** Lost because the same context that made a
  mistake is the one least able to see it.
- **Use the Capping gate's per-proposal judge.** Lost because it sees one action at a time, never
  whether the whole attempt meets the objective.
- **Give the Nurse the Worker's transcript or Handoff.** Lost because that hands it the Worker's
  assumptions, which is the context the review exists to escape.
- **Roll back a Virtual Cell on rejection.** Lost because most findings are fixable in place, and
  a rollback throws away the work that passed.
- **Escalate when no Nurse is eligible.** Lost because a small Hive with one model would page a
  human on every task; a flagged acceptance keeps the record without the noise.
