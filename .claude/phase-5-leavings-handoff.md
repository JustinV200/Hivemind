# Phase 5 Leavings handoff: what is on `feat/phase-5-leavings` and what a merge must know

> Roadmap 5.0a-5.0e, ADR-0025, the four real-run exit criteria, and the fixes the real runs
> forced, all on `feat/phase-5-leavings` (cut from `main` at 2563154, not pushed). The Virtual
> Cells half of phase 5 (5.1-5.13) is on `feat/phase-5-virtual-cells`, built in a separate
> worktree against these notes; its own handoff carries the merge plan. Merge this branch first.

## What landed (one commit per line, `git log --oneline main..HEAD`)

- 5.0a `cell/leavings/` ledger; `note_restore_path(..., persist, approved_by, reason)`; the
  releaser ledgers persisted paths (`cell.left`), `hive cells leavings list|remove`.
- 5.0b `PlannedTask.leaves` / `PlannedLeaving` (waggle 1.3) through Task, Assignment, brief.
- 5.0c+d `supervision/capping/leave/` policy over `leave-policy.toml`; `HumanCheck`; the Queen's
  keep-for-goal memory (`queen/leave_memory.py`); `ask` refuses the reserved options.
- 5.0e `keep` tool, `COPY` action (waggle 1.4), `[hive_stand] keep_root` (needs
  `access_level = "FULL"`), command before/after scan, `gate.py` became `gate/`.
- Fixes (2026-09-22): a silent judge is a FAILED check (`JudgeAnswerError`, `judge_error` on
  `capping.checked`), judge budget 4096, rubric `outside_scratch_write-v3`; zero-sub-bee grants
  are denied with `forage.denied` and fail the task; `RELEASING -> ORPHANED` so a failed
  `release()` can be retried; `leavings list` with no Cell lists every Cell, `remove --path`;
  the `#` on `ci.yml` line 1 restored.

## Contact points for the Virtual Cells branch

- `supervision/capping/gate/core.py` (`_run_checks`, `_apply_and_verify`) and `gate/model.py`
  (`GateDeps`: leave_policy, declared_leaves, keep_root, leave_home, human_timeout_s,
  disk_reserve_mb); `CappingGate.run(..., asker=None)`; `apply_action(..., ApplyExtras)`.
- `LeavingsStore`: `record_leaving` (upserts, keeps the original prior), `get_leaving`,
  `list_leavings`, `mark_removed`, `list_all_leavings`. The Undertaker adapter is
  `list_leavings(cell)` then `mark_removed` per row with a `cell.leaving_removed` event.
- `RealCellLease.note_allowed_path`; `LocalProcessSession` reads `lease.allowed_paths` live (an
  `InCellSession` must too). `worker_capabilities(..., extra_write_roots)`;
  `wardens/spawn/spawn.py::_prepare_capabilities` / `_widen_lease_reachability` (FULL only).
- `queen/dispatcher._send_grant_and_assign` returns `ForageGrant | None`;
  `tests/builders/queen.py` map source offers `FORAGE_SOURCE_SEATS = 4`.
- `QueenDeps.scratch_root/keep_root`, `WardenDeps.keep_root/leave_home/disk_reserve_mb`,
  `PlanBrief.scratch_root/keep_root`; `Queen.submit_goal` body is `queen/goal_submission.py`.
- Trail kinds added: `cell.left`, `cell.leaving_removed`, `capping.leave_decided`,
  `queen.leave_remembered`. Waggle 1.5 is reserved for the other branch.
- The leave policy reads `Cell.source == "hive_stand"`; a Virtual Cell takes the "borrowed"
  rows of `leave-policy.toml` until someone adds a row.

## Running it for real

Copy the operator's `hive.toml`, add `[hive_stand] access_level = "FULL"` and `keep_root`, keep
`[llm.slots.judge] max_output_tokens` (6144 on the 27B model), `lms ps` must show CONTEXT 16384,
`--timeout 900`. A keep goal takes about 5-8 minutes on this model; the judge runs on every
outside-scratch write and costs about 30 s each. Paths under the user profile are class HOME
(ALLOW on the Hive Stand); an executable or a path outside home reaches ASK. After a run,
`hive cells leavings list --manifest <copy>` shows every Cell's rows; `remove --path <file>`.
