# Phase 4 handoff: state, what it implemented, and how to test and debug it

> For an agent starting with a clean context. Phase 4 (memory, Forage, Clustering) was implemented
> on 2026-09-15/16 by an orchestrator dispatching one subagent per roadmap step, then cleaned up
> and tested for real later on 2026-09-16 (section 4 records what that pass found and fixed).

Read first, in this order: `CLAUDE.md`, `.claude/codingrules.md` sections 3, 4, 5, 8.9, 8.10,
8.12, 8.13, 17 and Appendix C, `.claude/roadmap.md` lines 688-845 (phase 4), `.claude/subagents.md`
(if you delegate), and the three ADRs `docs/adr/0022-*`, `0023-*`, `0024-*`.

## 1. Where things stand

- Branch: `feat/phase-4-memory-forage-clustering`, 42 commits ahead of `main`, **not pushed**.
  `git log --oneline main..HEAD` lists one commit per roadmap step, then the five follow-up fixes
  of 2026-09-16 (section 4.1) and this document.
- Every step 4.1-4.11 is ticked in the roadmap; the exit criteria are marked met, with a note at
  the end of the phase recording the real runs, including the real provider outage.
- Git identity is repo-local (`git config user.name/email` were unset globally).
- Line endings: the working copy is CRLF, git normalises to LF. Ignore the "CRLF will be replaced"
  warnings; do not "fix" them.

Gates, all green at handover (run from the repo root):

```
uv run ruff format --check . ; uv run ruff check . ; uv run mypy ; uv run lint-imports   # the whole repo, as CI does: ruff also formats Python blocks in markdown
uv run python scripts/check_sizes.py ; uv run python scripts/check_fanout.py
uv run python scripts/check_no_model_ids.py ; uv run python scripts/check_no_kind_branches.py
uv run python scripts/check_no_transcripts.py
uv run pytest -q -m "not integration and not e2e and not live_llm and not local_llm" -p no:cacheprovider   # 4890 passed
uv run pytest -q -m "not integration and not e2e and not live_llm and not local_llm" --cov --cov-report= -p no:cacheprovider   # CI runs with coverage; it is slower and once broke the fake-clock pump
uv run pytest -q -m e2e packages/hivemind/tests/e2e -p no:cacheprovider                                  # 52 passed, 4 runs in a row
```

## 2. What phase 4 implemented (map)

| Area | Modules | Notes |
|---|---|---|
| Relevance and packing | `memory/relevance.py`, `memory/hot_state/packing.py` | One pure `score`; `AssembleRequest.cells_in_play`, `on_drop` callback, `Prompt.dropped` |
| Bee Bread | `memory/bee_bread/`, `memory/store/sqlite/`, migrations 0002/0003 | Lookup-only warm tier; SQLite store became a package |
| Demotion, compaction, sweep | `memory/demote.py`, `memory/compact/`, `workers/roles/house_bee/` | Sweep runs from `queen/ticks/housekeeping.py` on `MemorySection.sweep_interval_s` |
| Cell Wax | `memory/cell_wax/`, `queen/autopilot/wax.py`, `queen/ticks/wax.py`, `wardens/requests.py` | Only the Queen writes; autopilot for a Warden's NOTE/CAUTION about its own Cell |
| Overflow and thresholds | `memory/overflow/`, `memory/thresholds/`, `queen/ticks/context.py` | `ContextOverflowError` maps to `AlarmKind.CONTEXT_OVERFLOW` in `workers/runtime/attempt.py` |
| Drone Handoff fidelity | `workers/roles/drone/outcome/` (package), `drone/sources.py` | Handoff lists derived from recorded tool calls; whole Handoff rendered on resume |
| Forage ledger | `queen/forage/ledger/` (book, seats, spend, decisions, recorder, SQLite store + .sql) | Headroom derived on read; `LedgerRecorder` implements the Fanner's recorder Protocol |
| Grants, requests, allocation v1 | `queen/forage/grants.py`, `requests.py`, `forage/allocate.py`, `queen/autopilot/forage.py`, `queen/ticks/forage.py` | Leases renewed on heartbeat, swept in `queen/ticks/liveness.py` (a fresh Queen revoked five stale grants of a dead Warden on start, seen for real); contested requests go to awake (`GRANT_BY_SHRINKING`) |
| Rate limits | `llm/models.py` (`RateLimitSnapshot`), both adapters, `forage/map.py` (`throttle`), `llm/fanner/` | `SpillReason.THROTTLED`; `llm.throttled` event |
| Hosting and ceilings | `queen/forage/hosting.py`, `ceilings.py`, `queen/dispatcher.py::_ensure_warden_provisioned` | Written on a Warden's first dispatch; Warden stores them via `wardens/ticks/control.py` |
| Clustering | `queen/state.py`, `queen/cluster/` (protocol, health, orders, tick, triggers) | Three triggers: operator order (`hive cluster`), cost cap, and **provider DOWN with no fallback** (added 2026-09-16, section 4.1); orders are SQLite rows the running Queen drains; kernel calls `ticks.housekeeping.run_housekeeping` |
| Judge and audit | `supervision/capping/checks/judge.py`, `rubrics.py`, `fake.py`, `capping/audit.py`, `wardens/judge.py`, `wardens/spawn/audited_gate.py` | Tier table: `judge = true` for outside_scratch_write, spend, device_command, irreversible; sampled audit only for scratch_write (0.02) and network_egress (0.1), which is the intended table (the toml's own comments say why) |
| CLI | `cli/memory/`, `cli/forage.py`, `cli/readback/cluster.py`, `cli/capping/` | See section 5 for spelling deviations |
| Tests | `tests/e2e/test_phase4_exit_criteria*.py`, `test_clustering.py`, `test_flood.py`, `tests/evals/handoff/` | Exit criteria one test per bullet |

`hivemind.queen.queen` sits at exactly the 300-LOC cap. Any new kernel hook goes through a
`queen/ticks/` module, never into `queen.py` itself.

## 3. How to run it for real

The operator's manifest is the repo-root `hive.toml` (gitignored): LM Studio on
`http://127.0.0.1:1234/v1`, model `qwen3.8-27b-heretic-abliterated-uncensored`, every slot on it,
`[llm.slots.queen] max_output_tokens = 12288` (section 4.1 says why). Check the server is up with
`curl -s http://127.0.0.1:1234/v1/models`; `lms server status` / `lms ps` also work (`lms` is at
`~/.lmstudio/bin/lms.exe`).

```
uv run hive run "write three short haiku about bees, each to its own file named haiku_1.txt, haiku_2.txt and haiku_3.txt, then confirm the three files exist" --manifest hive.toml --timeout 900
```

**Always pass `--timeout 900`.** The default is 120 s and the budget counts from submission,
planning included. With the default, the poll loop expires the instant dispatch happens, the
normal `run_hive` teardown stops the Warden, and the freshly spawned Drones' HTTP requests are
cancelled; that looked like a Drone regression once and cost an hour.

Real runs on 2026-09-16, all against the operator's manifest:

| Run | Planning | Drones | Result |
|---|---|---|---|
| Plain `hive run`, before the follow-up fixes | 5 attempts, 418 s | 3 in parallel | succeeded, one proposal rolled back and retried |
| Plain `hive run`, after fixes 2 and 3 but with the planner still capped at 8192 | 4 attempts, 6 min | 2 sequential tasks | succeeded in 407 s |
| Plain `hive run`, with the manifest's 12288 reaching the planner (fix 5) | 1 attempt, 78 s | 1 task | succeeded; this was the outage run below |

The provider-outage run (roadmap 4.9's exit criterion, for real): `hive run` in one process, then
`lms server stop` three seconds after the first `worker.spawned`, then `lms server start -p 1234`
once `queen.clustered` landed, then `hive wake --manifest hive.toml`. Trail, all in 55 s:

```
18:26:50 worker.spawned                  first Drone, mid-call when the server dies
18:26:53 alarm.raised WORKER_CRASHED     "Server disconnected without sending a response" -> RETRY, respawn
18:26:55 alarm.raised PROVIDER_UNAVAILABLE  the respawned Drone: ConnectError -> alarm.escalated, queen.decided ESCALATE_TO_HUMAN
18:27:19 warden.clustered, queen.clustered {cause: provider_down}, task.paused    26 s after the stop, no operator order
18:27:20 lms server start; 18:27:22 hive wake -> task.resumed, forage.granted, worker.spawned, queen.resumed
18:27:35 capping.* for the three haiku files (first and only time they are written), 18:27:43 task.succeeded, exit 0
```

No work was duplicated (both dead Drones died before their first write). The model stayed loaded
across `lms server stop`/`start`; if `lms ps` ever shows it gone, reload with
`lms load qwen3.8-27b-heretic-abliterated-uncensored --context-length 16384 --gpu max --parallel 4`.

Two traps met for real on 2026-09-20:

- **Check the loaded context after any LM Studio restart.** `lms ps` must show CONTEXT 16384. A
  restart reloaded the model at 8192; the planner's prompt plus schema is about 4000 tokens, so
  every attempt ran out of context at about 4140 reasoning tokens (`finish_reason: length`, empty
  text) and `hive run` failed after nine attempts and sixteen minutes with `MalformedOutputError
  ... ''`. Nothing in the manifest can see this: `/v1/models` does not report the loaded context
  (LM Studio's own `/api/v0/models` does, as `loaded_context_length`; a start-up check against
  `[llm.providers.*.capabilities] context_window` would be a cheap guard). Reload with the
  command above.
- **Never run the e2e suite while a real `hive run` is going.** The real-clock scenarios carry a
  10 s goal timeout and lost 4 then 11 tests to contention, on committed code too; the same suite
  was green the moment the real run ended.

Where to look when a run misbehaves:

- Trail, straight from SQLite (the run view hides `llm.*`, `memory.*`, `forage.*` kinds; the
  `at` column is UTC, LM Studio's log is local time, four hours behind on this machine):

```python
import sqlite3, json

con = sqlite3.connect(
    "file:hive.sqlite3?mode=ro", uri=True
)  # beside hive.toml; safe while a run holds it (WAL)
for at, kind, subj, body in con.execute(
    "select at, kind, subject_id, body from pheromone_events where at > '2026-09-16T18:00' order by at"
):
    print(at[11:23], kind, subj[:24], json.loads(body).get("payload"))
```

- LM Studio server log: `~/.lmstudio/server-logs/<YYYY-MM>/<date>.1.log`. Every request body is
  logged (`"max_tokens"`, `"reasoning_effort"`, the schema), every reply too: `finish_reason`,
  `reasoning_tokens`, the start of `reasoning_content`. "Reasoning setting 'medium' is not
  supported ... Supported settings: 'on', 'off'" is normal for this model (section 4.2).
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

## 4. Testing and debugging brief

### 4.1 What the 2026-09-16 clean-up pass found and fixed (one commit each)

1. **Scenario (g) flake was a real bug.** `tests/e2e/test_kernel_on_hive_stand.py::
   test_a_failing_command_proposal_is_rolled_back` failed about once in forty runs with two
   `alarm.raised` events, the second carrying "cannot start a transaction within a transaction"
   (or `transaction()`'s own guard). Every SQLite store ran its hops on `asyncio.to_thread`'s
   shared pool; when the Warden's respawn cancelled the old sub-bee's runtime mid-write
   (`wardens/ticks/alarms.retire_sub_bee`), the awaiting coroutine returned, the store's lock was
   released, and the pool thread was still inside the transaction when the next write began on the
   same connection. Fix: `hivemind.common.sqlite.ConnectionThread`, one single-worker executor per
   connection, used by all six stores; `tests/unit/common/test_sqlite.py` reproduces the race.
   Twenty consecutive runs of the scenario and four full e2e passes since.
2. **Planner burned its whole output budget thinking.** On the NATIVE and JSON_MODE rungs the
   model never saw the schema the provider enforced, so it reasoned about "the exact fields given
   separately" until `finish_reason=length` with 8192 reasoning tokens and no text; the ladder then
   said "reply was not valid JSON". Fix: `render_schema_hint` appends the schema as a user turn on
   every rung, and a `MAX_TOKENS` reply earns a correction that names the cut-off and asks for
   brevity. The planner prompt now says the schema follows.
3. **Tempo never reached the Fanner.** Sub-bee lanes and the judge's lane were built with
   `Tempo()`. `lane_for_grant` now takes the assignment's tempo; `JudgeRequest` carries the
   proposal's; `ModelJudgeReviewer` takes a lane factory (`Fanner.lane`).
4. **No health-driven Clustering existed.** `cluster()` had two callers: operator orders and cost
   caps. `run_cluster_tick` now probes every provider a live grant is bound to (GET `/v1/models`
   every 30 s while healthy, visible in LM Studio's log) and clusters one after two consecutive
   DOWN readings when some grant on it has no allowed binding on another available provider
   (`queen.clustered` carries `cause = "provider_down"`). DEGRADED is left to the Fanner.
5. **The manifest's per-slot `max_output_tokens` was read by nothing.** `cli/stores.slot_bindings`
   dropped it, so `PLANNER_MAX_OUTPUT_TOKENS = 8192` always won. The forage-side `SlotBinding`
   and `BoundModel` now carry it and `BoundModel.stamp` applies it in both gates. With 12288 the
   planner validated on its first attempt (78 s, 3 min faster than the best earlier run).

Also done: the two empty orphan lease directories were removed by hand (the sweep is roadmap 5.8,
the Undertaker: "on Queen startup sweeps orphans of both kinds"; `cell/lease_state.py` says so);
the CLI README's stale "no ModelJudgeReviewer exists" line was corrected.

### 4.2 Open observations

Second clean-up pass, 2026-09-20: every item that was open here is closed except the last one
below; the first one below is new, found while testing that pass.

Closed (one commit each, tests beside each):

- **A dead provider no longer costs a wasted respawn.** `llm/providers/openai_compat/client.py`
  maps `RemoteProtocolError`, `ReadError`, `WriteError` and `CloseError` to
  `ProviderUnavailableError`, so the bee in flight when the server dies raises the outage itself.
  The anthropic adapter already covered this (`APIConnectionError`).
- **A provider outage never reaches the human inbox.** A `PROVIDER_UNAVAILABLE` Alarm with no
  fallback binding used to fall through to `ESCALATE_TO_HUMAN` and stay in `hive inbox` after
  Clustering had handled the outage. `queen/ticks/alarms._cluster_or_retry` now probes the task's
  own providers at once (`queen/cluster/triggers.cluster_if_down`: the failed call plus one DOWN
  reading is enough), clusters what is down (`alarm.handled` with `action = "CLUSTER"`, resolved
  when the resumed task succeeds) and otherwise retries the task. The attempt-0 escalation at the
  Warden was by design: `policy.decide` never matches a fresh Alarm, so its default applies.
- **`[llm] default_max_output_tokens` is applied, and optional.** It is the cap for every slot that
  names none; `None` (the new default, was 4096) leaves each call site's own budget alone.
- **The `None` file**: closed, cannot come from the runtime (scratch confinement, recorded restore
  paths only; `connect(None)` makes a 4096-byte file, not an empty one).
- **Cancelled thread hops elsewhere**: reviewed. `cell/local/session.py` and `releaser.py` hold no
  lock across their `to_thread` calls, so the SQLite failure mode cannot occur; the worst case is
  a cancelled write finishing inside its own lease's scratch after its caller gave up.
- **`hive forage status` shows throttled sources**, rebuilt from the trail's `llm.throttled` events.
- **`hive run` starts its view at submission**; it used to replay the store's whole history.
- **`[hive_stand] keep_scratch`** (development only) leaves a run's files on disk and prints the
  lease directory; `cell.released` then honestly reports `is_restored = false`.

Still open:

1. **A grant of zero sub-bees parks its task silently until the run times out.** Found for real
   on 2026-09-20: a game plus the 17 GB model left 1.4 GB of free RAM, so `forage/allocate.py`
   correctly sized the grant at `max_sub_bees = 0` (free memory after the Royal Reserve, over the
   Drone's 512 MB footprint). The dispatcher issued it anyway (`queen/dispatcher.
   _send_grant_and_assign`), the Warden raised `GRANT_EXCEEDED` ("allows zero sub-bees"), the
   Queen escalated it to the human inbox, and the task sat RUNNING for the whole 900 s with no
   Drone and nothing in the run view saying why. The operator's `hive.toml` records the same hang
   from an earlier cause (one seat against a reserve of one), worked around in config. The fix is
   at that one choke point: a fresh grant with `max_sub_bees < 1` should record `forage.denied`
   with the allocator's own reason plus the free-memory figure and fail the task at once, so
   `hive run` ends in seconds with the cause on screen. It is not a one-liner: `builders.queen.
   make_queen_deps` itself yields zero-bee grants by default (one map seat, reserve of one;
   `test_overriding_reserve_unlocks_the_seat...` asserts it) and most Queen unit tests expect the
   assignment to go out regardless, so the fixture's default reserve has to change with it. Note
   too that a Cell's capacity is probed once, at lease time, so waiting for memory to free up
   would never help today; fail fast is the right v0 behaviour.
2. **This model's reasoning is the planning bottleneck, and the knob is binary.** LM Studio
   accepts `reasoning_effort` `none|minimal|low|medium|high|xhigh`, but for this GGUF only on/off
   exist: `none` turns thinking off (verified with curl: 0 reasoning tokens), everything else
   falls back to on. `Effort` has no NONE. Adding one changes `waggle.messages.forage.Effort`, a
   wire enum: a protocol minor bump (1.2 to 1.3), `docs/waggle/spec.md`, `forage/slots.py`,
   `forage/allocate.py`'s ceilings, `queen/autopilot/effort.py` and both adapters' tables. That is
   phase 8 (routing) work, not clean-up. Until then the 12288 budget makes planning reliable but
   slow (80 to 150 s per attempt on this machine).

### 4.3 Deliberate deferrals (each recorded in its commit message)

- Cell Wax reaches the goal-decomposition prompt only through placement (`queen/placement/
  decide.py` excludes BLOCK, penalises CAUTION) and the wax judgement episode; threading candidate
  Cells into the planner episode waits for phase 5.
- Workers propose wax through their Warden (`WorkerContext` has no fire-and-forget channel).
- `Ceilings` revision numbering is in-memory (`queen/forage/ledger/decisions.py`), restarts at 0.
- `ForageRequestKind.BINDING` requests are denied with a reason until phase 8 routing.
- `hive capping audit sample` requires `--fake-judge`: the trail carries no proposal content, so a
  model judge would review a placeholder (`cli/capping/sample.py`).
- `MemoryBudget.compact_at` defaults to 0.5 in `queen/deps.py`; there is no manifest field for it.
- `HostingPlan` is written and sent on first dispatch but nothing routes by it until phase 8.
- Not exercised for real: compaction on the ripener slot (`hive memory compact <bee>`), the
  housekeeping sweep inside a long run (first sweep is one `sweep_interval_s` after start,
  default 3600 s), `hive forage grant` revising a live grant while a Queen runs.

### 4.4 How to reproduce the important checks

- Flake hunt: `for i in $(seq 1 20); do uv run pytest -q -p no:cacheprovider -m e2e
  "packages/hivemind/tests/e2e/test_kernel_on_hive_stand.py::test_a_failing_command_proposal_is_rolled_back"; done`
  (each run about 13 s; use `--tb=long` to catch the frames, the short form hides them).
- Outage drill: run `hive run` in one shell, and in another, after `worker.spawned` appears in the
  streamed events: `lms server stop`; wait for `queen.clustered` (about 30 s); `lms server start
  -p 1234`; `hive wake --manifest hive.toml`; check `hive cluster status --manifest hive.toml`
  and `hive inbox --manifest hive.toml` afterwards.
- Planner attempts: count `llm.call` events with `subject_id` before the first `queen.planned`;
  each is one ladder attempt (the rung is not on the trail; LM Studio's log shows
  `response_format` for NATIVE/JSON_MODE and the fenced-block preamble for PROMPTED).
- Health probes: `grep "GET to /v1/models" ~/.lmstudio/server-logs/2026-09/<date>.1.log`, one per
  bound provider every 30 s while a goal runs.

## 5. CLI rough edges (documented in `cli/README.md`)

- `hive capping audit sample` is a subcommand, not `--sample`; `hive cluster --provider NAME`,
  not a positional (Click resolves a bare token against the argument before subcommands).
- Group-level options must precede the subcommand: `hive forage --manifest X status`,
  `hive cluster --manifest X status`, `hive memory wax --manifest X <cell> list`; but
  `hive memory pins list --manifest X` (leaf-level). Unify if it bothers you.
- `hive forage status` shows throttled sources from the trail's `llm.throttled` events; the live
  figure itself is only in the running Queen's `ForageMap`.

## 6. Suggested order for the next agent

1. Push the branch when the operator asks; open the PR with the real-run table from section 3.
2. Phase 5. Keep every file under the codingrules caps (`scripts/check_sizes.py` is strict), one
   commit per logical change, roadmap ticked in the commit that lands it.
3. Section 4.2 item 1 (the zero-bee grant hang) before any more real runs on a busy machine;
   `Effort.NONE` (item 2) rides with phase 8.
