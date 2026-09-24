# ADR-0034: Lowering a Honey label is a proposal an independent judge or the human decides, filed only when the Real Cell floor alone holds the label up

- Status: Accepted
- Date: 2026-09-24

## Context

Labels come from provenance at intake. Anything gathered on a Real Cell (a borrowed device, the
Hive Stand included), anything the human wrote and anything watch mode observed is `C2`. A model
may raise a label and never lower one. Lowering is a Capping proposal reviewed by the judge or a
human (codingrules 8.9, roadmap 7.3, ADR-0031). Phase 7 first wired only the human half:
`hive honey relabel`, one Honey row at a time, approver `HUMAN`.

That left a gap the phase's real runs made plain. Everything a Drone learns on the Hive Stand is
`C2` by the Real Cell floor, whatever it says. `hive run` defaults to `--clearance C1` so that an
ordinary goal never reads personal data unasked. A default run therefore never reads what earlier
runs learned on the Hive Stand. On 2026-09-24 the operator chose judge-reviewed lowering over the
alternatives below.

Four facts shape the design:

- **The Ripener already reads every deposit and labels it.** `RIPENER` returns a clearance with a
  reason (roadmap 7.5). But its prompt told it to repeat the current label whenever the text is no
  more sensitive, so on the Hive Stand its reading was always the floor's `C2`.
- **The judge is independent by rule.** It runs on `ModelSlot.JUDGE`, has its own rubric and
  shares no context with the proposer (codingrules 8.12, roadmap 4.10).
- **The Capping gate is built for Cells** (ADR-0018). Its actions (diff, command, action sequence,
  copy) apply through a `CellSession` and a lease. Its postconditions are file and command checks,
  and its judge request carries a `ProposedAction`. A label is a Honey Store row's state, not a
  Cell's, and none of those shapes can say "lower this Nectar to C1".
- **Import-linter keeps `honey_store` and `supervision` independent siblings at Layer 2.** The
  Honey Store cannot import the Capping package's state machine or judge models.

## Decision

**Who a judge may lower.** A Nectar (one deposit and the Honey rows ripened from it) is eligible
when all of these hold:

- It is ripened, not tainted, and not from a Night Veil Cell.
- Its origin is neither the human nor watch mode. Their `C2` is the content's own nature: a
  person's words, or observations of the operator's machine. Only the human lowers those, with
  `hive honey relabel` or `hive honey review`.
- Its label is held up by the Real Cell floor alone. Intake now keeps two facts it used to
  discard. `declared_clearance` is the highest label any depositor declared (or the default when
  none did), kept across dedupe merges. `floor_clearance` is the provenance floor. A Nectar is
  eligible only when `declared_clearance` ranks below its current label.
- The Ripener's own reading of the text (`ripener_clearance`) ranks below its current label.

The lowering's target is the higher of `declared_clearance` and the Ripener's reading, so a
judge never takes a label below what any depositor said. A Nectar written before this ADR has
none of the three facts and is never eligible. This rule is a pure function
(`honey_store/lowering/rules.py`). It is evaluated when a proposal is filed, and again inside the
transaction that applies one. A merge, a raise or a taint in between leaves the label where it is.

**The Ripener's honest reading.** `ripen_nectar.md` now asks for the content's own label,
independent of the current one. A label above the current one raises it at once, as before. A
label below it is stored as `ripener_clearance` and never lowers anything by itself. It can only
start a proposal. The Ripener's one-line reason is kept on the proposal for the human and never
shown to the judge.

**One proposal per Nectar, ever.** `honey_lowerings` (migration `0003`) holds each proposal: its
id, the Nectar, the from and to labels, its state, the approver, the Ripener's reason, the
verdict's reasons, the judge's rubric id, review attempts, a note for the human, and when it was
proposed and decided. Its state machine lives in one transition table
(`honey_store/lowering/state.py`, codingrules Appendix C):

- `PROPOSED -> LOWERED`: a judge's approval, or the human's.
- `PROPOSED -> REJECTED`: a judge's rejection, the human's denial, or eligibility lost when the
  proposal is applied.
- `REJECTED -> LOWERED`: the human only. The human is the last word in the chain (codingrules
  8.8).
- `LOWERED` is terminal. A later raise is an ordinary relabel.

A proposal is never re-filed after either outcome, so the judge is asked once per Nectar.

**The review.** The House Bee's ripening pass runs in the Queen's process, beside her and never
inside her tick. After it ripens, it files proposals for newly eligible Nectar, then asks the
judge about proposals still `PROPOSED`, a bounded number of each per pass (`[honey.lowering]`).

The judge is a `ClearanceJudge` protocol in `honey_store/lowering/`. The model-backed
implementation runs `complete_structured` on the `JUDGE` binding with its own prompt
(`judge_clearance.md`) and a versioned rubric id. The judge sees only the deposit's text, its kind
and media type, the target label and the label definitions. It never sees the Ripener's reason,
any id, or who asked. The text is delimited and labelled as untrusted data, so an instruction
inside it is data to judge, never an order.

- `APPROVE` lowers the Nectar.
- `REJECT` rejects the proposal.
- A judge that cannot answer (the structured-output ladder exhausted) leaves the proposal
  `PROPOSED` and counts an attempt. After `max_attempts` it waits for the human.
- A text longer than `max_judge_chars` is never sent to the judge, because a judge must see
  everything it clears. It waits for the human, with a note saying why.
- With no `JUDGE` binding, or `[honey.lowering] enabled = false`, every proposal waits for the
  human.

**Applying.** One transaction re-checks eligibility against the stored row. It then lowers the
Nectar and every Honey row of it still at the old label, moves the proposal to `LOWERED` with its
approver, and records `honey.label_lowered`: the from and to labels, the approver (`JUDGE` or
`HUMAN`), the proposal id and the rubric id, and never text. Reading the rows back in that
transaction is the postcondition. Nothing is half applied. Filing records `honey.lowering_proposed`
and a rejection records `honey.lowering_rejected` (the approver and the outcome). Like every other
`honey.*` kind, these are written on the Queen's own node. No Night Veil Nectar is ever eligible,
so none of them concerns a Night Veil Cell.

**The operator.** `hive honey review` lists proposals, waiting ones first, with the Ripener's
reason, the judge's reasons, attempts and notes. `hive honey review approve <id> --reason` lowers
as `HUMAN` from `PROPOSED` or `REJECTED`. `hive honey review deny <id> --reason` rejects as
`HUMAN`. `hive honey review --judge` runs the judge over waiting proposals now, the way
`ripen --now` runs a pass. `hive run --clearance` keeps its `C1` default. Its help says that Honey
learned on the Hive Stand reaches a `C1` goal once it is lowered, or at once with `--clearance C2`.

**Why not the Capping gate itself.** This is Capping's discipline without `CappingGate`'s Cell
vocabulary. The Ripener proposes, the proposer never approves, an independent judge or the human
checks, the write applies and verifies in one transaction, and every edge is on the trail. Teaching
`CappingGate` a label action would add a Waggle action kind that no bee ever sends over the wire,
an apply path with no session, and postconditions with no Cell. Keeping lowering in the Honey
Store also keeps its policy next to the store's exact filters.

## Consequences

Positive: Honey learned on the Hive Stand that holds nothing personal becomes readable by a
default `C1` goal once an independent judge agrees, with the chain of custody on the trail. The
Real Cell floor stays exactly as strict at intake, and the human keeps the last word both ways.
A Hive with no `JUDGE` binding loses nothing: proposals wait in `hive honey review`.

Negative: every eligible Nectar costs one judge call. A judge that approves wrongly exposes
Hive Stand material to `C1` readers, and Night Veil Cells read `C1` too. Pin `JUDGE` to a
different provider or model than `RIPENER` so the two do not share blind spots, the same advice
as for the Capping judge. Set `[honey.lowering] enabled = false` for a Hive that should never
lower without the human. Summary and chunk text is labelled as a whole Nectar, so one personal
line keeps the whole deposit `C2`.

## Alternatives considered

- **Default `hive run` to `C2`**: every goal would read and send the operator's personal data by
  default, and hosted providers would see it.
- **An operator-declared intake floor for the Hive Stand** (`[hive_stand] honey_floor = "C1"`):
  it breaks the rule that anything from a Real Cell is `C2` for everything the machine produces,
  personal or not, and no reviewer looks at any of it.
- **Document `--clearance C2` only**: the same exposure as the first alternative, left to the
  operator to remember.
- **Let the Ripener lower**: a model deciding data is less sensitive is exactly the failure the
  label exists to prevent (ADR-0031).
- **A `RELABEL` action through `CappingGate`**: see above.
- **Judge each Honey row separately**: a Nectar's rows share one label, and a split verdict would
  expose part of a deposit whose other part the judge found personal.
