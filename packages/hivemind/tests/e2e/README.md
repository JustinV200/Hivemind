# hivemind end-to-end tests

Whole-Hive scenarios that run the Queen, at least one Warden and its Workers together, the way
`hive run "goal"` would from the CLI. These are the slowest and fewest tests in the tree; they
exist to catch problems that only show up when every layer runs at once.

- `test_phase2_exit_criteria.py` (`@pytest.mark.e2e`) -- the phase 2 exit criteria (`.claude/
  roadmap.md`): submits a three-task graph through `hive tasks submit`
  (`typer.testing.CliRunner`), drives it through every task status including BLOCKED and PAUSED
  via a `BroodChamber` built directly in-process, "restarts" by reopening the store on the same
  SQLite file and checking identical state through both the chamber and `hive tasks show`, and
  checks the trail is complete and in order; a second test merges two nodes' trail segments
  through `hive trail export`/`merge` and checks the merged log has no duplicate ids and is
  ordered by `(at, node_id)`. `scripts/brood_demo.py` is the same scenario run as real child
  processes, for a human to watch.
- `test_kernel_on_hive_stand.py` (`@pytest.mark.e2e`) -- roadmap step 3.22's eight whole-Hive
  kernel scenarios, each parametrised over `capabilities in ("full", "none")` and run once
  through a single Hive (no retry loop anywhere in this module: the kernel fix-forward commit
  closed the real-SQLite-timing races the checkpoint suite's own retry loops were guarding
  against):
    - **(a)** the three-haiku goal completes, in the trail order the kernel's own structure
      actually guarantees, both in-process and through `hive run` itself.
    - **(b)** a hand-killed Drone is respawned by Warden autopilot alone, no Queen awake episode.
    - **(c)** a Drone that crashes three times walks the full escalation ladder (a Warden RESPAWN,
      two escalations) to the Queen, who REBINDs it to the manifest's own fallback slot and the
      goal finishes there -- the trail's own `queen.decided(REBIND)` names the fallback binding,
      and the goal is proven to have actually run on it.
    - **(d)** a Drone's question blocks the task until one `hive inbox answer` (CliRunner) resumes
      it. Ran on the manifest's own default heartbeat cadence: the second kernel fix-forward
      commit's own fix 3 (one answer-forwarding rule, called from both the Queen's tick and
      `run_goal`'s poll loop) closed the race a slower `heartbeat_interval_s` used to paper over;
      see the test's own docstring for the exact race and which fix closed it.
    - **(e)** a scripted checkpoint resumes and finishes from its own Handoff.
    - **(f)** a write outside scratch is rejected and never lands, and the goal still finishes.
    - **(g)** a failed `run_command` proposal is rolled back, the goal still finishes, and its
      whole Alarm chain reaches the trail: `alarm.raised` (kind `POSTCONDITION_FAILED`, raised by
      the Worker) and `alarm.handled` (action `RETRY`, the Warden's own policy dispatch one hop
      later). The second kernel fix-forward commit's own fix 1 (the Worker now records
      `alarm.raised`) and fix 2 (a pending Alarm is flushed before every terminal WorkerState
      transition) together close the gap the previous suite's own xfail here documented; see the
      module's own docstring for the exact mechanics.
    - **(h)** the left-as-found snapshot holds -- an unchanged tree, empty scratch, every started
      pid dead.

  Scripted through `tests.e2e.kernel_helpers.HaikuScript` over one `hivemind.llm.fake.
  FakeLLMProvider`, against a real Hive Stand lease and real SQLite (never `pump_until_done`'s own
  `FakeClock`, unlike `tests.unit.cli.test_compose`'s in-process counterpart of scenario (a)).

## Budget

All sixteen parametrised cases (eight scenarios × two capability levels; nine test functions, one
of which -- scenario (a) -- has both an in-process and a `hive run`-CLI form) run in under twenty
seconds on this host (roadmap step 3.22's own exit criterion), typically well under it -- see
`--durations=10` below for the slowest individual cases on a given run. No case retries and none
is xfailed: the two kernel fix-forward commits closed every race this suite once had to work
around instead of assert through.

## What each scenario asserts

No case in `test_kernel_on_hive_stand.py` retries and none is xfailed; every one below runs once
per capability level and asserts real trail events (and, where named, filesystem or process state)
rather than only `report.succeeded`.

| Scenario | Trail events (and other state) asserted |
|---|---|
| (a) | `cell.leased`/`queen.planned`/`queen.assigned` in order; `capping.proposed`/`capped`/`applied`/`verified` in order; `task.succeeded` before `cell.released`; `forage.granted` and `worker.spawned` present (see the table below for what each means); scratch empty after release; the CLI form's own output also names `task.succeeded`. |
| (b) | `worker.spawned` at least twice (the respawn); `queen.awake` never appears. |
| (c) | `worker.failed` exactly 3 times; `worker.spawned` at least 4 times; `alarm.escalated` present; exactly one `queen.decided` with `action="REBIND"` naming `binding="local_worker"`; the goal actually ran on the fallback model id. |
| (d) | the task reaches `BLOCKED` before `hive inbox answer` is called; the goal succeeds once the CLI answers it. |
| (e) | `worker.handing_off` and `worker.resumed` both present; the `memory.checkpoint` event's own id resolves to a real, storable Handoff. |
| (f) | `capping.rejected` present; the file the rejected write targeted never exists on disk. |
| (g) | `capping.rolled_back` present; scratch holds exactly the three real haiku files (nothing the rolled-back command touched lingers); exactly one `alarm.raised` with payload `kind="POSTCONDITION_FAILED"`; exactly one `alarm.handled` with payload `action="RETRY"`. |
| (h) | every pid the lease started is dead; the Hive Stand's own tree outside the SQLite data dir is byte-for-byte unchanged; scratch is empty. |

## What the order assertions check

Scenario (a) asserts that certain trail event kinds appear, in a required relative order, as one
Hive runs a goal to completion. One line each, in the order they appear on a clean run:

| Kind | Meaning |
|---|---|
| `cell.leased` | The Warden acquired its Cell lease from the Hive Stand. |
| `queen.planned` | The Queen decomposed the goal into its task graph. |
| `queen.assigned` | The Queen placed a task with a Warden and a Cell. |
| `forage.granted` | The Warden's own Forage allocation (bindings, `max_sub_bees`) was recorded. |
| `worker.spawned` | A sub-bee (a Drone) was spawned to run the task. |
| `capping.proposed` | A Drone proposed an action through the Capping gate. |
| `capping.capped` | The proposal passed its pre-apply checks (schema, size cap). |
| `capping.applied` | The proposal's action was actually applied. |
| `capping.verified` | The proposal's declared postconditions held after applying. |
| `task.succeeded` | The task's Warden-verified outcome was recorded as SUCCEEDED. |
| `cell.released` | The Warden released its Cell lease, left exactly as found. |

Run just this suite with:

```
PYTHONIOENCODING=utf-8 uv run --frozen pytest -m "e2e" packages/hivemind/tests/e2e -q --durations=10
```
