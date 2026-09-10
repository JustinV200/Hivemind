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
  kernel scenarios, each parametrised over `capabilities in ("full", "none")`: (a) the three-haiku
  goal completes, in the trail order the kernel's own structure actually guarantees, both
  in-process and through `hive run` itself; (b) a hand-killed Drone is respawned by Warden
  autopilot alone, no Queen awake episode; (c) a Drone that crashes three times escalates to the
  Queen, whose own retry finishes the task (a `Queen`-level REBIND to a stronger slot is xfailed:
  see the module docstring for the exact gap); (d) a Drone's question blocks the task until `hive
  inbox answer` (CliRunner) resumes it; (e) a scripted checkpoint resumes and finishes from its own
  Handoff; (f) a write outside scratch is rejected and never lands, and the goal still finishes;
  (g) a failed `run_command` proposal is rolled back (a mismatched `write_file` postcondition is
  unreachable through any shipped tool, and no component raises an Alarm on rollback: both xfailed,
  see the module docstring); (h) the left-as-found snapshot holds -- an unchanged tree, empty
  scratch, every started pid dead. Scripted through `tests.e2e.kernel_helpers.HaikuScript` over one
  `hivemind.llm.fake.FakeLLMProvider`, against a real Hive Stand lease and real SQLite (never
  `pump_until_done`'s own `FakeClock`, unlike `tests.unit.cli.test_compose`'s in-process
  counterpart of scenario (a)). Several scenarios (`_retry_goal`, and scenario (d)'s own retry
  loop) retry with a fresh Hive on a rare, real race this module's own docstring names: real SQLite
  I/O lets the Queen's own post-dispatch bookkeeping and a fast Warden/Drone reaction interleave in
  ways the FakeClock-driven unit test never exercises.

Run just this suite with:

```
PYTHONIOENCODING=utf-8 uv run --frozen pytest -m "e2e" packages/hivemind -q --durations=10
```
