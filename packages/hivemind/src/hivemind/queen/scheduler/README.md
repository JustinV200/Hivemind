# hivemind.queen.scheduler

The Queen's scheduling policies beside placement. Swarming (roadmap step 13.3,
`docs/adr/0063-swarming-policy-and-cost-caps.md`) is the first.

## Public API

- `SwarmingPolicy`, `BackendLimit`: `[virtual_cells.swarming]` as a value object: `max_parallel`
  across every backend, `max_cost_per_hour_usd`, and per backend `max_cells`, `cost_per_hour_usd`
  and `draws_on_hive_stand`. Every field unset by default, so the default policy caps nothing.
- `SwarmView`, `BackendLoad`: what the dispatcher measured before a placement: ready tasks, each
  backend's Cells held and being made, and the Hive Stand's room in fresh Cells.
- `plan_swarm(view, policy) -> SwarmPlan`: per backend, how many fresh Cells it may start now
  (the smallest bound that is set) and, for a zero, the reason (`swarming: max_parallel 2
  reached`). `hivemind.queen.dispatcher.snapshot` marks such a backend held back with it, so
  placement skips it and the task waits PENDING for a later pass.

Pure: no I/O, no clock, nothing from `hivemind` imported, and `hivemind.llm` forbidden to the
package by the same import contract as autopilot.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/queen/scheduler -q
uv run --frozen pytest packages/hivemind/tests/e2e/test_swarming.py -q
```
