# Phase 4 handoff: test, debug and fix

> For an agent starting with a clean context. Phase 4 (memory, Forage, Clustering) was implemented
> on 2026-09-15/16 by an orchestrator dispatching one subagent per roadmap step. Everything below
> is what that orchestrator knew at handover, including what it did not get to verify.

Read first, in this order: `CLAUDE.md`, `.claude/codingrules.md` sections 3, 4, 5, 8.9, 8.10,
8.12, 8.13, 17 and Appendix C, `.claude/roadmap.md` lines 688-840 (phase 4), `.claude/subagents.md`
(if you delegate), and the three ADRs `docs/adr/0022-*`, `0023-*`, `0024-*`.

## 1. Where things stand

- Branch: `feat/phase-4-memory-forage-clustering`, 27 commits ahead of `main`, working tree
  clean, **not pushed**. `git log --oneline main..HEAD` lists one commit per step; each message
  names what that step deliberately left out.
- Every step 4.1-4.11 is ticked in the roadmap; the exit criteria are marked met with a note at
  roadmap line ~831 that records the real runs and the one timed-out run.
- Git identity is repo-local (`git config user.name/email` were unset globally).
- Line endings: the working copy is CRLF, git normalises to LF. Ignore the "CRLF will be replaced"
  warnings; do not "fix" them.

Gates, all green at handover (run from the repo root):

```
uv run ruff format --check packages ; uv run ruff check ; uv run mypy ; uv run lint-imports
uv run python scripts/check_sizes.py ; uv run python scripts/check_fanout.py
uv run python scripts/check_no_model_ids.py ; uv run python scripts/check_no_kind_branches.py
uv run python scripts/check_no_transcripts.py
uv run pytest -q -m "not integration and not e2e and not live_llm and not local_llm" -p no:cacheprovider   # 4867 passed
uv run pytest -q -m e2e packages/hivemind/tests/e2e -p no:cacheprovider                                  # 52 passed
```

## 2. What phase 4 added (map)

| Area | Modules | Notes |
|---|---|---|
| Relevance and packing | `memory/relevance.py`, `memory/hot_state/packing.py` | One pure `score`; `AssembleRequest.cells_in_play`, `on_drop` callback, `Prompt.dropped` |
| Bee Bread | `memory/bee_bread/`, `memory/store/sqlite/`, migrations 0002/0003 | Lookup-only warm tier; SQLite store became a package |
| Demotion, compaction, sweep | `memory/demote.py`, `memory/compact/`, `workers/roles/house_bee/` | Sweep runs from `queen/ticks/housekeeping.py` on `MemorySection.sweep_interval_s` |
| Cell Wax | `memory/cell_wax/`, `queen/autopilot/wax.py`, `queen/ticks/wax.py`, `wardens/requests.py` | Only the Queen writes; autopilot for a Warden's NOTE/CAUTION about its own Cell |
| Overflow and thresholds | `memory/overflow/`, `memory/thresholds/`, `queen/ticks/context.py` | `ContextOverflowError` maps to `AlarmKind.CONTEXT_OVERFLOW` in `workers/runtime/attempt.py` |
| Drone Handoff fidelity | `workers/roles/drone/outcome/` (package), `drone/sources.py` | Handoff lists derived from recorded tool calls; whole Handoff rendered on resume |
| Forage ledger | `queen/forage/ledger/` (book, seats, spend, decisions, recorder, SQLite store + .sql) | Headroom derived on read; `LedgerRecorder` implements the Fanner's recorder Protocol |
| Grants, requests, allocation v1 | `queen/forage/grants.py`, `requests.py`, `forage/allocate.py`, `queen/autopilot/forage.py`, `queen/ticks/forage.py` | Leases renewed on heartbeat, swept in `queen/ticks/liveness.py`; contested requests go to awake (`GRANT_BY_SHRINKING`) |
| Rate limits | `llm/models.py` (`RateLimitSnapshot`), both adapters, `forage/map.py` (`throttle`), `llm/fanner/` | `SpillReason.THROTTLED`; `llm.throttled` event |
| Hosting and ceilings | `queen/forage/hosting.py`, `ceilings.py`, `queen/dispatcher.py::_ensure_warden_provisioned` | Written on a Warden's first dispatch; Warden stores them via `wardens/ticks/control.py` |
| Clustering | `queen/state.py`, `queen/cluster/` (protocol, health, orders, tick, triggers) | Orders are SQLite rows the running Queen drains; kernel calls `ticks.housekeeping.run_housekeeping` |
| Judge and audit | `supervision/capping/checks/judge.py`, `rubrics.py`, `fake.py`, `capping/audit.py`, `wardens/judge.py`, `wardens/spawn/audited_gate.py` | Tier table: `judge = true` for outside_scratch_write, spend, device_command, irreversible |
| CLI | `cli/memory/`, `cli/forage.py`, `cli/readback/cluster.py`, `cli/capping/` | See section 4 for spelling deviations |
| Tests | `tests/e2e/test_phase4_exit_criteria*.py`, `test_clustering.py`, `test_flood.py`, `tests/evals/handoff/` | Exit criteria one test per bullet |

`hivemind.queen.queen` sits at exactly the 300-LOC cap. Any new kernel hook goes through a
`queen/ticks/` module, never into `queen.py` itself.

## 3. How to run it for real

The operator's manifest is the repo-root `hive.toml`: LM Studio on `http://127.0.0.1:1234/v1`,
model `qwen3.8-27b-heretic-abliterated-uncensored`, every slot on it. Check the server is up
with `curl -s http://127.0.0.1:1234/v1/models`.

```
uv run hive run "write three short haiku about bees, each to its own file named haiku_1.txt, haiku_2.txt and haiku_3.txt, then confirm the three files exist" --manifest hive.toml --timeout 900
```

**Always pass `--timeout 900`.** The default is 120 s and the budget counts from submission,
planning included; this model plans in 2-7 minutes (the structured ladder retried five times
once). With the default, the poll loop expires the instant dispatch happens, the normal
`run_hive` teardown stops the Warden, and the freshly spawned Drones' HTTP requests are cancelled.
That looked like a Drone regression and cost an hour: the trail shows `worker.spawned` then
`warden.stopped` 26 ms later, and LM Studio logs "Client disconnected". Three long-timeout runs
all succeeded with three Drones in parallel (about 5 minutes wall time each).

Where to look when a run misbehaves:

- Trail, straight from SQLite (the run view hides `llm.*`, `memory.*`, `forage.*` kinds):

```python
import sqlite3, json
con = sqlite3.connect("hive.sqlite3")   # beside hive.toml; table pheromone_events, column body is the event JSON
for at, kind, subj, body in con.execute("select at, kind, subject_id, body from pheromone_events where at > '2026-09-16T04:00' order by at"):
    print(at[11:23], kind, subj[:24], json.loads(body).get("payload"))
```

- LM Studio server log: `~/.lmstudio/server-logs/<YYYY-MM>/<date>.log` ("Received request",
  "Client disconnected", "Generated prediction", context overflow errors).
- Instrumented run: build the Hive in a script with `build_hive(manifest, environ, clock,
  responders={})` outside any running loop, then `async with run_hive(hive): await run_goal(...)`.
  Monkeypatch `AttemptManager.request_cancel / force_cancel_if_due / cancel_role_task` and
  `WorkerRuntime.stop / _handle_pause / _on_transport_closed` to print `traceback.format_stack()`;
  those are the only paths that cancel a Drone's model call. A mixed manifest (copy `hive.toml`,
  add `[llm.providers.fake] kind = "fake"`, point every slot except `worker` at `fake`, set
  `offline = false`, and pass a responder for the QUEEN slot that returns a plan) runs in under a
  minute and is the fastest way to exercise real Drones.
- `tests/e2e/kernel_helpers.py` has the scripted fake (`HaikuScript`, `plan_response`,
  `judge_approve_response`) and `capture_encoded_envelope_sizes`.

## 4. Known issues and open items

Bugs or observations to investigate first:

1. **Flaky under load**: `tests/e2e/test_kernel_on_hive_stand.py::test_a_failing_command_proposal_is_rolled_back`
   (scenario g) failed intermittently only while several agents ran concurrently; passes alone
   and in quiet full-suite runs. The file's own docstring calls it a real-clock race. Reproduce
   with `pytest -m e2e ... --count` style repetition under CPU load before touching it.
2. **A zero-byte file named `None`** appeared in the repo root at 00:16 on 2026-09-16, right after
   the timed-out run and while the new CLI commands were being smoke-tested. It never reproduced
   (every new command re-run on the operator db and on a fresh db, including the misplaced-option
   usage error) and was deleted. Only `cell/local/session.py::_write_file`,
   `cell/local/releaser.py::_replay_restore_records` and `cli/trail.py` write arbitrary paths.
3. **Orphan lease directories**: `.hive/scratch/lease_01M2KF40...` and `lease_01M2KGHX...` (empty,
   from 2026-09-15) survive every run. Appendix C's "orphan sweep on Queen and Warden start" is
   not implemented. Decide whether phase 4's left-as-found rule owns that or phase 13 does.
4. **Planning quality on the local model**: the QUEEN slot needed five `complete_structured`
   attempts (112 s, 54 s, 109 s, 72 s, 69 s) to produce a valid plan once. Check the
   `llm.fallback` / ladder rungs on the trail and the planner schema; phase 8 owns local-model
   routing but a prompt fix is cheap now.
5. **Sampled audit is effectively off**: the wiring step set `judge = true` on four tiers and
   reset their `audit_rate` to 0.0 in `supervision/defaults/capping-tiers.toml`; only
   `scratch_write` keeps a nonzero rate. Confirm that is the intended table.
6. **Per-grant Fanner lanes use `Tempo()`** (NORMAL, no latency budget): `wardens/deps.py::
   lane_for_grant` and the judge reviewer's lane. A task's real tempo never reaches the Fanner's
   queue ordering or spill threshold. The exit-criteria seat test orders by tempo only because it
   builds lanes directly.
7. **Not exercised for real**: a live provider outage through `hive cluster` / `hive wake`
   (kill LM Studio mid-run, then `hive wake`), compaction on the ripener slot against the real
   model (`hive memory compact <bee>`), the housekeeping sweep firing inside a long real run
   (first sweep is one `sweep_interval_s` after start, default 3600 s), `hive forage grant`
   revising a live grant while a Queen runs.

Deliberate deferrals (each recorded in its commit message; not bugs, but confirm they are still
the right call):

- Cell Wax reaches the goal-decomposition prompt only through placement (`queen/placement/
  decide.py` excludes BLOCK, penalises CAUTION) and the wax judgement episode; threading
  candidate Cells into the planner episode waits for phase 5.
- Workers propose wax through their Warden (`WorkerContext` has no fire-and-forget channel).
- `Ceilings` revision numbering is in-memory (`queen/forage/ledger/decisions.py`), restarts at 0.
- `ForageRequestKind.BINDING` requests are denied with a reason until phase 8 routing.
- `hive capping audit sample` requires `--fake-judge`: the trail carries no proposal content, so a
  model judge would review a placeholder (`cli/capping/sample.py`).
- `MemoryBudget.compact_at` defaults to 0.5 in `queen/deps.py`; there is no manifest field for it.
- `HostingPlan` is written and sent on first dispatch but nothing routes by it until phase 8.

CLI rough edges (documented in `cli/README.md`):

- `hive capping audit sample` is a subcommand, not `--sample`; `hive cluster --provider NAME`,
  not a positional (Click resolves a bare token against the argument before subcommands).
- Group-level options must precede the subcommand: `hive forage --manifest X status`,
  `hive cluster --manifest X status`, `hive memory wax --manifest X <cell> list`; but
  `hive memory pins list --manifest X` (leaf-level). Unify if it bothers you.
- `hive forage status` cannot show throttled sources (throttle state lives only in the running
  Queen's `ForageMap`).

## 5. Suggested order

1. Run the full gate list from section 1 on a quiet machine, then the e2e suite three times in a
   row to size the scenario (g) flake.
2. Run the real goal with `--timeout 900`; then kill LM Studio mid-run, restart it, write a wake
   order (`hive wake --manifest hive.toml`) and check the trail for `queen.clustered` →
   `memory.checkpoint` → `PAUSED` → `queen.resumed` and no duplicated file writes.
3. Work through section 4 items 1-7 in order; each is small except the flake.
4. Keep every file under the codingrules caps (`scripts/check_sizes.py` is strict), one commit
   per logical change, roadmap ticked in the commit that lands it, and push only when asked.
