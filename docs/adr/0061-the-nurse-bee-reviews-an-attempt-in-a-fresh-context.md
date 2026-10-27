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
implementation lives in `wardens/nurse.py`; a `FakeNurseReviewer` lives beside the Protocol. The
Queen's rule reads only the wire fields below.

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

- **`TaskAssign.review_round` is added** beside `review_findings` (0 to 7, default 0). The design
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

### Deviations from the design pass, made in step 2 (the seam)

- **`FakeNurseReviewer` lives in `supervision/capping/checks/fake.py`,** beside
  `FakeJudgeReviewer`, not in `wardens/nurse.py`. Codingrules 14.4 puts a fake beside its
  Protocol, and the Protocol is in `checks/nurse.py`; a Warden-layer fake would also be out of
  reach of any test below the Warden layer.
- **`NurseAnswerError` lives in `supervision/capping/errors.py`** with `JudgeAnswerError`, the
  package's one error tree. It carries the chosen model's `nurse_grade`, because the wire requires
  a grade on an ERROR review, and the failed call's `spend`, because that cost is still the
  attempt's.
- **The reviewer returns a `NurseAnswer`, not a bare `NurseVerdict`:** the verdict, the grade of
  the model that gave it, and the call's spend. No verdict and no grade means no model was
  eligible, so UNAVAILABLE is an answer, not an error. `run_nurse(reviewer, request, round_)`
  maps an answer or a `NurseAnswerError` onto the wire's `NurseReview` and returns the spend to
  add to `TaskResult.spend`, so the Warden never hand-builds a review. An outage still
  propagates, as it does from the judge.
- **`NurseRequest` carries `worker_grade`, `worker_providers` and `tempo`.** One reviewer serves
  every task on a Warden, so these per-task facts travel in the request. The reviewer filters
  eligible models by the grade, prefers a provider outside `worker_providers` (the providers of
  the Worker's bindings and of the models its `llm.call` events reached, at most 16), and orders
  its call by the tempo; none of them tells the Nurse how the Worker reasoned. It names no Worker
  and no rubric: the prompt is the model Nurse's own (step 3).
- **Bounds.** At most 64 actions (`MAX_NURSE_ACTIONS`), 64 artifacts (`MAX_ARTIFACTS`, as on
  `TaskResult`), 32 files (`MAX_NURSE_FILES`), 16 providers (`MAX_NURSE_PROVIDERS`) and 100,000
  characters of file text in all (`MAX_NURSE_EVIDENCE_CHARS`), a hard ceiling the manifest's
  `max_evidence_chars` sits beneath. The goal reuses the judge's `MAX_GOAL_CHARS`.
  Every outcome's index must name a criterion in the request.

### Deviations from the design pass, made in step 3 (the model Nurse)

- **`wardens.nurse` is a package, not one module:** `strength` (the Worker's grade), `eligible`
  (which JUDGE links may review), `prompt`, `verdict` (the reply and its translation) and
  `reviewer` (`ModelNurseReviewer`). Five jobs do not fit one file under codingrules 5.2, and
  `wardens/` already holds more than ten modules.
- **`NurseRequest` gains `spend_left`,** what the task's grant has left (None when uncapped). The
  budget pre-check needs a per-task figure, and one reviewer serves every task on a Warden.
- **Eligibility is more than grade.** A JUDGE link is eligible only when the Forage map grades it
  at or above the Worker, the estimated prompt plus reply fits its context window, and the cost
  of one attempt (estimated input tokens and the reply budget, at the link's own prices) fits
  `spend_left`. That is a pre-check on one attempt, not a cap on the review: the structured-output
  ladder may retry a link several times and then fall back along the chain, so the real spend can
  be a few times that figure. The Fanner meters every attempt against the grant, which is the real
  bound. A link failing any check is skipped; no link left is the "not nursed" UNAVAILABLE
  answer, never an escalation, the same as having no strong model at all. An unpriced link passes
  the budget check, since its cost cannot be estimated; the Fanner still meters it.
- **The eligible links are re-linked into a chain of their own,** another provider first, the
  manifest's order otherwise, so the ladder may still fall back but only onto an eligible model.
  The ladder does not report which link answered, so the review records the lowest grade in that
  chain: never a stronger Nurse than the one that may have answered.
- **Unknown grades fail toward a stronger Nurse.** A JUDGE link the Forage map cannot grade is
  never eligible. A Worker model the map cannot grade sets the Worker's grade to the top grade,
  so only a top-grade Nurse (the identical model included) can review it, and otherwise the
  attempt is flagged "not nursed".
- **Spill is read from the chains, and calls only catch what they miss.** A Fanner spill only
  walks the binding's own fallback chain, which the Worker's grade already covers link by link. An
  `llm.call` event names its slot and provider but never its model (the trail keeps model ids off
  it), so an event whose (slot, provider) matches a link of the Worker's bindings adds nothing.
  So the caller must pass every binding the attempt ran on, rebind targets included (the old and
  new sub-bee bindings, or `llm.rebound`'s to_binding): a rebind that stays on the same (slot,
  provider) is invisible in the calls. Only an event matching no link (a rebind onto another
  slot or provider that the bindings missed) counts, as the strongest model the map lists on its
  provider. Grading every event that way would overstate the Worker, and overstating is not safe:
  a higher Worker grade leaves fewer eligible Nurses and more attempts accepted "not nursed",
  with no review at all. Non-chat calls (EMBEDDER, TRANSCRIBER) are ignored. Putting the binding
  key on `llm.call`, as `llm.spill` already carries it, would make the rebind case exact too; it
  is left for later.
- **The reply is translated fail-closed.** The model answers in a looser private schema;
  `to_verdict` keeps each finding that has a real anchor (a criterion in range, or a path) and
  non-empty text, bounded to the wire's limits, and drops the rest. Any usable finding makes the
  verdict CHANGES_REQUESTED, whatever the model said; CHANGES_REQUESTED with none is a
  `NurseAnswerError` (an ERROR review), because it cannot steer a fix. The verdict's `rubric_id`
  is always `nurse-review/1`, never model text.
- **A refused or over-long call is an answer failure too.** `RefusedError` and
  `ContextTooLongError` become `NurseAnswerError` beside `MalformedOutputError`, so the accept path
  records ERROR instead of crashing; outages still propagate. A failed ladder reports zero spend,
  because the ladder does not return the usage of attempts that failed.

### Deviations from the design pass, made in step 4 (the Warden wiring)

- **The attempt is every bee that worked on it.** A Warden-side respawn (a retry, a rebind, a
  Handoff successor) retires one sub-bee and starts the next on the same Cell, and the last one
  claims. Each `SubBee` carries a `NurseTrace` (`wardens.nurse.trace`): the binding every bee ran
  on and the Capping gate it proposed through, carried from a retired bee to its successor. The
  Worker's grade is taken over every one of those bindings, which is how step 3's "rebind targets
  included" is met, and the actions are every bee's own VERIFIED proposals (a gate's proposals
  whose proposer is that gate's bee), newest 64 kept. `CappingGate` gains a read-only
  `proposals()` for this. A Queen retry is a new attempt and starts a new trace.
- **Which files are read, and how.** The paths are the verified actions' `paths` followed by the
  claim's artifacts, each once, the first 32. An artifact is only the Worker's word, and the
  Warden's lease reaches further than the bee's own set, so an artifact is read only inside
  scratch or where the Guard would let the sub-bee read it itself: `bee_may_read` asks the
  Warden's `Enforcer` the request the Worker's own `read_file` makes outside scratch (the Worker
  as principal, its own set, its Cell, at `session_outside_scratch`), so every floor refuses
  first, the Hive-state floor's database, secret store and manifest included, then the access
  level, the deny list and the held set. A refusal is a `guard.denied` row like any other, and
  its escalation is never acted on: an unreadable artifact is skipped, never asked about. A
  verified action's paths already passed the bee's gate, floors included.
  `CellSession` has no bounded read, so each file is read whole through the Warden's session,
  only the first `4 * budget` bytes are decoded, and the text is cut to what is left of
  `max_evidence_chars`. A Swarm device refuses a file over 16 MiB itself; on a local Cell the
  read is whole, as acceptance's own file checks already are, and a ranged read is left for
  later. A file is skipped, never an error, whenever it cannot be read: out of the lease, gone,
  a directory, a NUL in its first KiB (binary), or any Cell, permission or transport error (a
  device's size limit or wall, a bad transfer, a dropped link). The machine checks have already
  passed, so reading evidence can thin what the Nurse sees but never fail the attempt; a skipped
  file is still named by its action. The text that fits is scanned as `SESSION_OUTPUT`, with the
  Warden as the reading bee, the sub-bee's capability set as the task's own hosts, and the path
  as the locator; a flagged file reaches the Nurse inside a harder fence, a dropped one as a
  withheld notice (`memory.render_untrusted`).
- **Reads before retiring, the model call after.** The files are read while the sub-bee is still
  held (the same rule acceptance follows), and the Nurse's model call runs after the bee is
  retired, so a slow review never holds a pool slot.
- **The Worker's calls are found by grant.** Every sub-bee lane stamps its grant on `llm.call`,
  so the Warden reads the newest 2,000 `llm.call` rows and keeps those carrying the task's grant.
  `spend_left` is the grant's budget less its `spent` and the claim's own spend; no grant held
  means no cap.
- **An outage is "not nursed".** The seam lets `ProviderUnavailableError` and `RateLimitedError`
  propagate; the Warden's accept path turns them into an UNAVAILABLE review (logged by class),
  the same as no eligible model, rather than ending its loop or escalating.
- **An ERROR review is accepted, flagged.** The design names the UNAVAILABLE case only. A Nurse
  that was chosen but gave no verdict is treated the same way: the attempt reaches SUCCEEDED with
  the ERROR review on it, recorded as `capping.nurse_reviewed` with that status.
- **JUDGE_RUBRIC, exactly.** The machine verdict the Warden decides on first leaves out every
  `JUDGE_RUBRIC` criterion, and the Nurse sees those criteria with no machine outcome ("judge it
  yourself"). A PASS holds them; CHANGES_REQUESTED rejects the attempt like any other finding.
  When no Nurse judged them (UNAVAILABLE or ERROR), they fail closed as before this ADR, with an
  ACCEPTANCE_FAILED Alarm naming the review's status: the "accept not nursed" rule covers a review
  of work the machine checks already proved, never a criterion nothing checked. With the Nurse
  off, or for a skipped role, `JUDGE_RUBRIC` fails as it always did. The planner still refuses to
  plan `JUDGE_RUBRIC`; letting it, where the Queen knows a Nurse will run, is left for later
  (step 5, below, says why).
- **A Night Veil Cell's evidence stays local.** Roadmap step 10.3a binds a Night Veil task to
  local models only, and the Nurse reads that task's files, so on a Night Veil Cell the Nurse is
  asked only when every link its JUDGE chain can reach serves locally (`binding_is_local` on the
  `judge` row, failing closed on a chain it cannot follow). Otherwise the round is UNAVAILABLE
  with no call: accepted "not nursed", or failed closed for a `JUDGE_RUBRIC` criterion.
- **A rejection is a FAILED result with reason `nurse`,** the review attached, no
  ACCEPTANCE_FAILED Alarm and no typed cause: the Queen's rule (step 5) reads the review itself.
- **The trail events carry the Cell.** Both events carry `cell_id` beside the round, the grades,
  and (when reviewed) the status, outcome and finding count, so a Night Veil teardown can find
  and drop them with the Cell.
- **Where `[nurse]` reaches.** The Hive Stand's Warden and a Swarm device's (both built from the
  manifest) get the section as written; `max_rounds` is 1 to 7, the wire's `review_round` ceiling.
  A Virtual Cell's or colonized device's Warden boots with no manifest, so it runs on
  `NurseWiring`'s defaults whenever its slot table binds JUDGE, the same way it takes the shipped
  tier table: `[nurse] enabled = false` does not reach a Cell yet. Carrying the section through
  the `HIVEMIND_*` environment is left for later.

### Deviations from the design pass, made in step 5 (the Queen's loop)

- **What `nurse_rounds` counts, and the cap.** `Task.nurse_rounds` counts the rejections the
  Queen has already sent the task back for. Her table reads a rejection as `RETRY_TASK` while
  `[nurse] max_rounds` less that count is above zero, and as `ESCALATE_TO_HUMAN` once it is not:
  with the default 2, the first two rejections are fixed forward and the third goes to the human.
  The table stays pure: the caller hands it the rounds left as `AttemptTally.nurse_left`, read
  from the stored Task (so the cap holds across her restart), never from her in-memory book or
  from the Warden's `review.round`, which a broken or hostile Warden could hold at 1 forever. The
  ordinary attempt ceiling plays no part in a Nurse rejection; the round cap is its bound.
  `nurse_rejected`, the table's own test (a `CHANGES_REQUESTED` verdict on the wire), is public so
  the Queen carries out exactly the rule the table applied: a FAILED result with an UNAVAILABLE or
  ERROR review (a `JUDGE_RUBRIC` criterion nobody judged) is retried like any failure and never
  counted as a round.
- **The column is a mirror.** Migration 0002 adds `tasks.nurse_rounds` as a column beside the
  row's JSON body, which stays the source of truth every read decodes, as `status` already is.
  `BroodChamber.note_nurse_round` counts one more on a RUNNING task and records a new
  `task.nurse_round` kind carrying only the new count (classified as a lifecycle notice).
- **Every assignment carries the count.** `TaskAssign.review_round` is the task's `nurse_rounds`
  on every send (a fresh dispatch, an ordinary retry, a re-placement, a resume), not only on a
  Nurse retry, so a Warden numbers its next review past it whatever kind of retry came between.
  `review_findings` rides only on a Nurse retry. The round is counted in the chamber before the
  assignment is built.
- **At the cap the task also fails.** The design names the escalation only. A rejected attempt's
  bee is already retired, so a task left RUNNING would wait out its goal's timeout with nothing
  on it and no lever for the human to pull. The Queen raises `NURSE_UNRESOLVED` (severity
  WARNING, origin the Warden whose Nurse rejected it, `attempts` the rounds spent, the task's
  clearance) and fails the task, cancelling its dependents as any failure does; the Cell keeps the
  last attempt's work, as every round has.
- **The findings reach the human inbox alone.** A Nurse's findings are untrusted model prose
  written from untrusted evidence, and the trail keeps no such words. The Alarm itself has a
  detail of counts only, so every reader of it stays clean: the trail's `alarm.escalated` (with
  the Cell named so a Night Veil teardown routes it) and `alarm.resolved` rows, the chat line,
  the devices' notice and the Queen's own awake episodes, which list the Alarms waiting. The
  findings travel as a note `HumanInbox` keeps beside the Alarm (`add_alarm(alarm, note)`,
  `note_for`), inside a `nurse_findings` fence labelled untrusted and cut to 2,000 characters with
  the fence still closed; the Entrance's inbox shows it after the detail, and it goes when the
  human acknowledges the Alarm.
- **A Night Veil task's findings stay on its Cell.** Its Nurse runs on local models and writes
  from that Cell's own files, but `HumanInbox` is in memory on the Queen and is not one of the
  side channels a Night Veil teardown purges, so a note there would outlive the Cell (codingrules
  section 12, "Nothing else crosses the boundary"). For a task asked for at, or bound to, the
  NIGHT_VEIL tier the Queen raises the same counts-only Alarm with no note; the human reads the
  findings on the Cell, if at all, while it still stands.
- **The fixing bee's brief.** The bounded-loop brief renders `review_findings` after the Scout's
  recon, inside the same kind of fence, each anchored to its criterion (in the words the bee
  already reads its checks in) or its file and line, and tells the bee the last attempt's work is
  still on the Cell to fix where it stands.
- **The "not nursed" note is a list, not a digest.** There is no inbox digest to add a line to.
  `hive inbox` gains a NOT NURSED section (and `not_nursed` in `--json`): the newest 20 rounds
  recorded as `capping.nurse_unavailable` or as `capping.nurse_reviewed` with status `ERROR`, by
  task, round and why, read from the trail. It is a note to glance at, never an Alarm. The
  Entrance's remote inbox does not list them yet; that is left for later.
- **A task that moved on is left alone.** Both the send-back and the escalation re-read the task
  and do nothing unless it is still RUNNING, so a rejection that lands after a cancel or a pause
  never revives work the Queen already closed.
- **The planner still refuses `JUDGE_RUBRIC`.** The Queen cannot yet know whether the Warden of
  the Cell a task lands on will run a Nurse (`[nurse] enabled` does not reach an in-Cell Warden,
  step 4, and a slot table may bind no judge), and a `JUDGE_RUBRIC` criterion nobody judges fails
  closed every attempt. Letting the planner plan one is left for later.
- **The real run.** `tests/e2e/test_nurse_on_hive_stand.py` runs the whole loop on the Hive Stand
  with a scripted model that answers each bee only from what it is shown: the Nurse rejects a
  file, the Queen sends the task back, a fresh Drone fixes it from the findings in its brief, and
  the second review passes.

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
