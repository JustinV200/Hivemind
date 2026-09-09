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
  ordered by `(at, node_id, id)`. `scripts/brood_demo.py` is the same scenario run as real child
  processes, for a human to watch.
