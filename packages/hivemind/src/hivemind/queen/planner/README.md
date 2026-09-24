# hivemind.queen.planner

The planner package is how the Queen decomposes a goal into the task graph the Brood Chamber
(the Hive's task store) then persists. Roadmap step 3.18: every planned subtask carries at least
one acceptance postcondition; where nothing machine-checkable exists, the planner emits a
`JUDGE_RUBRIC` criterion instead.

## Public API (roadmap step 3.20)

- `PlanSchema`, `PlannedTask`, `PlannedPostcondition` (`schema.py`): the JSON schema a model fills
  to decompose one goal. `PlannedPostcondition` re-runs `waggle.messages.Postcondition`'s own
  cross-field rules by building one, so a weak model's near-miss is a ladder retry carrying the
  broken rule, not a schema mismatch before the ladder even runs.
- `PlannedTask.leaves` (roadmap step 5.0b): a bounded tuple of `waggle.messages.PlannedLeaving`
  (an absolute or `~`-rooted path, a one-line reason), empty by default. `PlannedLeaving`'s own
  validator refuses a bare root/drive or a `..` segment; this schema adds the one rule it cannot
  check itself -- a pattern inside the Hive Stand's own scratch -- read from
  `pydantic.ValidationInfo.context["scratch_root"]`, which only `plan_goal` ever supplies.
- `plan_goal` (`plan.py`): renders `decompose_goal.md` with the goal folded in as the `USER`
  section, asks a model for a `PlanSchema` through `hivemind.llm.complete_structured` (passing
  `PlanBrief.scratch_root` on as that call's validation context), and converts the reply into a
  validated `hivemind.brood_chamber.TaskGraphDraft` -- acyclic, unique keys, every subtask
  carrying acceptance, all enforced by `TaskGraphDraft`'s own construction. Raises `PlannerError`
  for a plan that cannot be turned into one.
- `PlanBrief.honey` (roadmap step 7.9): what the Queen's consultation of the Honey Store (the
  Hive's ripened, searchable knowledge) found for the goal, as `waggle.messages.honey.HoneyHit`s.
  `plan_goal` renders them into the prompt's `RETRIEVED` section with `hivemind.memory`'s own
  preamble and hit blocks, labelled reference data and never instructions; a hit above the goal's
  clearance is never shown, and no section is rendered when nothing was retrieved. The
  consultation itself runs in `hivemind.queen.goal_submission` through
  `hivemind.queen.dispatcher.consult_for_plan`.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/queen/planner packages/hivemind/tests/unit/queen/test_goal_submission.py -q
```
