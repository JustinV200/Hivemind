# hivemind.queen.planner

The planner package is how the Queen decomposes a goal into the task graph the Brood Chamber
(the Hive's task store) then persists. Roadmap step 3.18: every planned subtask carries at least
one acceptance postcondition; where nothing machine-checkable exists, the planner emits a
`JUDGE_RUBRIC` criterion instead.

## Public API (roadmap step 3.20)

- `PlanSchema`, `PlannedTask`, `PlannedPostcondition` (`schema.py`): the JSON schema a model fills
  to decompose one goal. `PlannedPostcondition` is deliberately more relaxed than
  `waggle.messages.Postcondition` (no cross-field validators), so a weak model's near-miss is a
  ladder retry, not a schema mismatch before the ladder even runs.
- `plan_goal` (`plan.py`): renders `decompose_goal.md` with the goal folded in as the `USER`
  section, asks a model for a `PlanSchema` through `hivemind.llm.complete_structured`, and
  converts the reply into a validated `hivemind.brood_chamber.TaskGraphDraft` -- acyclic, unique
  keys, every subtask carrying acceptance, all enforced by `TaskGraphDraft`'s own construction.
  Raises `PlannerError` for a plan that cannot be turned into one.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/queen/planner -q
```
