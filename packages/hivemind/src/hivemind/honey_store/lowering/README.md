# hivemind.honey_store.lowering

Judge-reviewed label lowering (ADR-0034). Anything gathered on a Real Cell (a borrowed device, the
Hive Stand included) is `C2` in the Honey Store by the provenance floor, whatever it says, so a
default `C1` goal never reads it. This package lets that label come down, and only this way: the
Ripener's own reading of the text starts a proposal, an independent judge on the `JUDGE` model slot
or the human decides it, the store applies and verifies it in one transaction, and every edge is on
the Pheromone Trail.

## Who may be proposed, and to what

`lowering_target(nectar)` (`rules.py`, pure) is the whole rule. A Nectar is eligible when it is
RIPENED, not tainted, not from a Night Veil Cell, of neither HUMAN nor WATCH origin, all three of
its labelling facts are known (`declared_clearance`, `floor_clearance`, `ripener_clearance`), the
floor alone holds its label up (the floor reaches it, no depositor declared it) and the Ripener's
reading ranks below it. The target is the higher of the declared label and the reading, so a judge
never takes a label below what any depositor said. It is evaluated when a proposal is filed and
again inside the transaction that applies it; the store's candidate scan mirrors it in SQL.

## The flow

```text
House Bee pass:  ripen ──► file_proposals ──► review_pending ──────────────┐
                              │ lowering_candidates                        │ pending_lowerings
                              │ + lowering_target                          ▼ (PROPOSED, no note)
                              ▼                                  text over max_judge_chars, or
                 add_lowering (PROPOSED,                         unreadable ──► note: waits for
                 honey.lowering_proposed)                        the human
                                                                           │ else
                                                                           ▼
                                                    ClearanceJudge.judge(text, title, kind,
                                                    media type, target, RUBRIC_ID)
                          APPROVE ──► apply_lowering (re-check; LOWERED + honey.label_lowered,
                                      or REJECTED "no longer eligible" + honey.lowering_rejected)
                          REJECT  ──► reject_lowering (REJECTED + honey.lowering_rejected)
                          no answer ─► attempts + 1; at max_attempts, note: waits for the human
hive honey review:  decide(id, approve, reason) as HUMAN: approve from PROPOSED or REJECTED,
                    deny from PROPOSED; the reason is kept on the proposal, never on the trail
```

A proposal's machine (`state.py`): `PROPOSED -> LOWERED` (judge or human), `PROPOSED -> REJECTED`
(judge, human, or eligibility lost at apply time), `REJECTED -> LOWERED` (the human only);
`LOWERED` is terminal. One proposal per Nectar, ever. A lowering holds for every repeat of the same
text: a Real Cell deposit that dedupes onto a lowered Nectar never brings back the floor the judge
cleared, while a higher declared label, or a HUMAN or WATCH origin, still raises it
(`store/sqlite/nectar.py`, ADR-0034).

## Public API

- **Rule** (`rules.py`): `lowering_target`, built on `held_by_floor_alone`, which lives in
  `hivemind.honey_store.clearance` beside `HUMAN_ONLY_ORIGINS`. Ripening asks that predicate too,
  so a deposit the floor alone holds up is read by the Ripener however short it is.
- **State machine** (`state.py`): `LoweringState`, `TRANSITIONS`, `TERMINAL_STATES`,
  `can_transition`, `assert_transition` (raises `LoweringTransitionError`).
- **Models** (`models.py`): `LoweringProposal` (the stored proposal), `LoweringId`,
  `LoweringFiling`/`LoweringDecision` (what the service hands the store), `ClearanceJudgeRequest`/
  `ClearanceVerdict`/`ClearanceOutcome` (what crosses to and from the judge) and their bounds.
- **Judge** (`judge.py`): `ClearanceJudge` (the seam), `ModelClearanceJudge(bound, gate)` (one
  `complete_structured` call on the JUDGE binding, prompt `judge_clearance.md`, verdict stamped
  with `RUBRIC_ID`; an exhausted ladder, a refusal, an oversized prompt or a timeout raises
  `ClearanceJudgeAnswerError`, an outage propagates).
- **Fake** (`fake.py`): `FakeClearanceJudge`, answers from a script and records every request.
- **Events** (`events.py`): `proposed_events`, `applied_events`, `rejected_events`, the builders
  the store calls inside its transactions; payloads carry labels, the approver, the outcome
  (`REJECT` or `INELIGIBLE`) and ids, never text or a reason.
- **Service** (`review.py`): `LabelLowering(LoweringDeps(store, identity, clock, settings,
  judge=None))` with `file_proposals() -> int`, `review_pending() -> ReviewOutcome` and
  `decide(proposal_id, approve, reason) -> LoweringProposal`.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/honey_store/lowering \
    packages/hivemind/tests/contracts/test_honey_store_lowering_contract.py
```

`test_rules.py` walks every eligibility clause (legacy NULLs, HUMAN and WATCH origins, Night Veil,
taint, a raised declared label, a target equal to the label); `test_state.py` walks every allowed
edge and asserts every forbidden one raises; `test_judge.py` snapshot-tests the rendered request
and scripts a `FakeLLMProvider` (approve, reject, a malformed reply, a refusal, a timeout, an
outage); `test_review.py` runs the whole flow on a real SQLite store with `FakeClearanceJudge`. The
contract suite runs every store method over all three store harnesses, including the apply
transaction's eligibility re-check and its read-back postcondition. On real models,
`tests/evals/honey/test_clearance_judge_local.py` (`local_llm`) asks the judge about a Hive Stand
outcome and a build log it must approve and four texts it must reject; run it for any model meant
for the JUDGE slot and around any rubric change (a 3B judge rejected everything; bind a different,
stronger model than the Ripener's). Coverage:

```bash
COVERAGE_FILE=.coverage.lowering uv run --frozen pytest -p no:cacheprovider \
    --cov=hivemind.honey_store.lowering --cov-report=term-missing \
    packages/hivemind/tests/unit/honey_store/lowering \
    packages/hivemind/tests/contracts/test_honey_store_lowering_contract.py
```
