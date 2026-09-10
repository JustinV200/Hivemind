# hivemind.queen.placement

The placement package is the Queen's pure decision of whether a task's TaskNeeds are met by
reusing a Real Cell or provisioning a new Virtual one.

## Public API (roadmap step 3.20)

- `Placement`, `PlacementError`, `decide` (`decide.py`): v0 places every task on the first
  attached `WardenLink` (the Hive Stand), refusing when `TaskNeeds.isolation` is `REQUIRED` (no
  Cell can isolate in v0) or the task's `os` need does not match that Cell's own capabilities.
  Never branches on `cell.kind` -- both checks read `TaskNeeds` and `CellCapabilities` only.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/queen/placement -q
```
