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
      it. This scenario tunes `heartbeat_interval_s` up (`fake_manifest`'s own parameter) to pin
      down a specific interleaving between the Queen's own tick loop and `hive inbox answer`'s own
      two separate writes; see the test's own docstring for the exact race and why slowing the
      heartbeat cadence (not retrying) is what makes it deterministic.
    - **(e)** a scripted checkpoint resumes and finishes from its own Handoff.
    - **(f)** a write outside scratch is rejected and never lands, and the goal still finishes.
    - **(g)** a failed `run_command` proposal is rolled back, the goal still finishes, and the
      Warden's own handling of the Alarm it raises (`alarm.handled`) reaches the trail. One gap
      remains here, xfailed: no component ever records `alarm.raised` for a Worker-originated
      Alarm (a crash, or this scenario's own Capping rollback) -- only a Warden's own *self*-raised
      Alarms do. See the module's own docstring and this scenario's own xfail reason for the exact
      missing call site.
    - **(h)** the left-as-found snapshot holds -- an unchanged tree, empty scratch, every started
      pid dead.

  Scripted through `tests.e2e.kernel_helpers.HaikuScript` over one `hivemind.llm.fake.
  FakeLLMProvider`, against a real Hive Stand lease and real SQLite (never `pump_until_done`'s own
  `FakeClock`, unlike `tests.unit.cli.test_compose`'s in-process counterpart of scenario (a)).

## Budget

All sixteen parametrised cases (eight scenarios × two capability levels) plus the two xfails run
in under twenty seconds on this host (roadmap step 3.22's own exit criterion), typically well
under it -- see `--durations=10` below for the slowest individual cases on a given run.

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
