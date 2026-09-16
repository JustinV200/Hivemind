# hivemind.queen.placement

The placement package is the Queen's pure decision of whether a task's TaskNeeds are met by
reusing a Real Cell or provisioning a new Virtual one.

## Public API (roadmap step 3.20)

- `Placement`, `PlacementError`, `decide` (`decide.py`): v0 places every task on the first
  attached `WardenLink` (the Hive Stand), refusing when `TaskNeeds.isolation` is `REQUIRED` (no
  Cell can isolate in v0) or the task's `os` need does not match that Cell's own capabilities.
  Never branches on `cell.kind` -- both checks read `TaskNeeds` and `CellCapabilities` only.
  Roadmap step 4.2a adds two optional keyword args, both empty by default: `blocked_cells`
  excludes a candidate outright (a WRITTEN `BLOCK` Cell Wax note), `cautioned_cells` keeps a
  candidate but ranks it behind every clean one (a WRITTEN `CAUTION` note is a penalty in
  ordering, never an exclusion). `decide` still reads no store of its own; the caller precomputes
  both sets from `hivemind.memory.cell_wax`.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/queen/placement -q
```
