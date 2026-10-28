# The chaos suite

Roadmap step 13.6. The chaos suite breaks one thing at a time on purpose against a whole Hive and
checks that the Hive copes. It lives in `packages/hivemind/tests/e2e/chaos/` and is marked `chaos`
(and `e2e`), so the push CI leaves it out and `integration.yml` runs it every night.

## The bar every scenario meets

`tests/e2e/chaos/rig.py` holds every scenario to the same three rules:

1. **Nothing hangs.** Each scenario runs under `bounded`, 90 seconds at most. A wedge fails the
   test with the scenario's name instead of stalling the job.
2. **Every goal ends explicitly.** Its tasks end SUCCEEDED, FAILED or CANCELLED, or a goal that
   could not be planned is refused at submission with the planner's own error. A task left
   PAUSED is a deliberate hold (Clustering, an isolation), and the scenario says so.
3. **Nothing is left open.** Once `run_hive` has returned, the trail shows no lease leased and
   never released and no grant granted and never revoked or expired, the Queen's Forage ledger
   holds no live grant, and no Virtual Cell is left on a backend.

## What each scenario breaks

| Module | What breaks | What must hold |
|---|---|---|
| `test_chaos_provider.py` | The fake model refuses, rate-limits, answers malformed tool JSON or a malformed plan, or exhausts the plan ladder, once each | The goal ends explicitly; nothing open |
| `test_chaos_provider.py` | The provider goes down while a Drone's reply is in flight | The health poller clusters it (checkpoint, then pause), sees it healthy again and resumes the task, with no operator order |
| `test_chaos_cells.py` | A Virtual Cell's container dies mid-task | The Queen takes the task back once the Warden is lost (`max_offline_s` plus the redial grace in a real Hive), tears the dead Cell down, and the task finishes on a fresh Cell |
| `test_chaos_cells.py` | Every Virtual Cell's link drops mid-task | The Warden redials and the task finishes |
| `test_chaos_pressure.py` | A rogue Warden link floods the Queen with thousands of Alarms mid-goal | The flood is held back, never dropped, and the goal still succeeds |
| `test_chaos_pressure.py` | Every Drone call costs far past the goal's spend cap | The cost cap clusters the provider, the task pauses at a Handoff, and stopping the Hive revokes its grant |
| `test_chaos_supersedure.py` | The candidate dies before it is ready, or stalls its restore | The old Queen rolls back inside the window and the goal finishes on her |
| `test_chaos_supersedure.py` | The old Queen's process ends mid-copy | She refuses to start until `hive supersede --settle`; settled as aborted she starts and the goal finishes |
| `test_chaos_entrance.py` | An unknown client hammers the invite route | The Guard Bee's `invite_abuse` rule reduces the Entrance; the remote listener stops answering |
| `test_chaos_injection.py` | Seeds from the shipped pattern file arrive through a Honey hit, and through a tool result in a Virtual Cell | The hit is flagged and withheld, steered writes and hosts are refused, no grant widens; the Cell is isolated by rule and its tainted Handoff is not resumed after a lift |
| `test_chaos_swarm.py` | A colonized device's Warden ships a segment signed with the wrong key, and one claiming another node | Both refused, nothing merged, the link stays up |
| `test_phase11_exit_nuc.py` (chaos leg) | The Nuc's cable is pulled mid-task | Once it is back, no lease the device held and no grant of those tasks is open |

## Running it

```
uv run --frozen pytest -m "chaos and not integration" packages/hivemind/tests/e2e/chaos -q
```

The whole suite takes about two minutes. The Nuc leg also needs Docker and the device image, and
is marked `integration`; without a daemon it skips. Run it with `pytest -m "chaos and integration"`
on a machine with Docker running.

## When a scenario fails

Each assertion names what it found: the scenario that hung, the leases or grants left open by id,
or the tasks that never ended with their status. A lease or grant left open after the Hive stopped
is a real leak: look at what the stop does for that kind of holder
(`hivemind.queen.dispatcher.ready.unfinished` for grants, `hivemind.cli.compose.hive.run` for the
order things stop in). A hang is usually an await with no bound on something the scenario broke.

Three of the suite's first findings were fixed in the Hive itself. A Virtual Cell killed mid-task
left its task RUNNING until the Hive stopped (`hivemind.queen.requeening.lost` now takes it back).
A stopping Hive left a paused task's grant live in the ledger (the stop now revokes it). A
Supersedure whose candidate stopped answering mid-copy held the old Queen SUPERSEDING for ever (the
copy is now bounded by the grace window).
