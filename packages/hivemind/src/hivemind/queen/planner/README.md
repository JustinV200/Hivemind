# hivemind.queen.planner

The planner package is how the Queen decomposes a goal into the task graph the Brood Chamber
(the Hive's task store) then persists. Roadmap step 3.18: every planned subtask carries at least
one acceptance postcondition; where nothing machine-checkable exists, the planner emits a
`JUDGE_RUBRIC` criterion instead. Roadmap steps 6.9/6.10: every planned subtask also names a
Worker role (`decompose_goal.md` says when to plan a Scout ahead of the Foragers that depend on
it, a Forager for web or GUI work, and a Drone for everything else).

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
- `PlannedTask.role` (roadmap steps 6.9/6.10): which Worker role the Warden spawns; defaults to
  DRONE and must be one of `hivemind.brood_chamber.task.model.PLANNABLE_ROLES` (DRONE, FORAGER,
  SCOUT), the same set `TaskSpec`/`TaskDraft` check their own `role` against. Two more
  planning-only rules: a FORAGER subtask requires `needs.exoskeleton` (it has no page or screen
  to act on otherwise), and a SCOUT subtask's acceptance must be exactly one `FILE_EXISTS`
  criterion on `waggle.messages.task.recon.SCOUT_REPORT_FILE` -- its own report, relative to its
  lease's scratch. Both are ladder-retryable `ValueError`s, exactly like every other rule here.
- `plan_goal` (`plan.py`): renders `decompose_goal.md` with the goal folded in as the `USER`
  section, asks a model for a `PlanSchema` through `hivemind.llm.complete_structured` (passing
  `PlanBrief.scratch_root` on as that call's validation context), and converts the reply into a
  validated `hivemind.brood_chamber.TaskGraphDraft` -- acyclic, unique keys, every subtask
  carrying acceptance, all enforced by `TaskGraphDraft`'s own construction. Raises `PlannerError`
  for a plan that cannot be turned into one.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/queen/planner -q
```
