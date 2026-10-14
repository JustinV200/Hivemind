# hivemind.wardens.watch

The watch package is Watch mode: a Warden with no active Workers observes its Cell read-only and
reviews it on a schedule (a Patrol) instead of sitting idle (ADR-0054). It applies to the Hive
Stand's own Warden and to every Swarm device's Warden.

## Public API (roadmap step 11.10)

- `PatrolChoice`, `PatrolDecision`, `PatrolOutcome`, `PatrolWaxSeverity` (`decision.py`): the one
  structured value a Patrol's episode produces (ALARM, NECTAR, WAX or NOTHING, a reason, and the
  text to keep) and how a Patrol ends (the four, or SKIPPED).
- `WatchDeps`, `WatchSettings`, `WatchSchedule` (`deps.py`): what a Warden is built with to watch.
  `WardenDeps.watch` is one optional field; `WatchSettings` is `[supervision.watch]`;
  `WatchSchedule` is the one mutable part (when the next sample and Patrol are due, the file-change
  cursor, and where the last reviewing Patrol stopped).
- `WatchProbe`, `PROBE_FAILURES`, `SessionWatchProbe` (`probe/`): how it looks. See the probe
  README.

## Modules imported by path

These import the Warden or the model machinery, so the package face leaves them out (the face is
imported by `hivemind.wardens.deps`):

- `observer.py`: `WatchObserver.sample` asks the probe for the kinds `hivemind.guard.watch_permits`
  allows (logs and file changes only when the operator named `roots`), under a timeout, and keeps
  the answer with `store`. A look that fails is logged by type and tried again at the next
  interval; one that finds nothing is not kept.
- `store.py`: an Observation as a `C2` `WATCH_OBSERVATION` Bee Bread entry (halved until it fits),
  swept past `retention_s` each time one is saved; read back oldest first; discarded by a Patrol
  that found nothing.
- `signals.py`: the pure pre-filter: a new process over `cpu_share_alarm`, the disk past
  `disk_fill_alarm`, and a restart loop, as typed `WatchSignal`s (labelled facts, never verdicts).
- `digest.py`: the Patrol's two texts. `summary_of` is the Hive's own framing (counts and times
  only); `facts_of` is what the machine said, every string JSON-quoted and cut, bounded.
- `patrol.py`: `Patrol.run` and the `PatrolPort` it drives. A decision that was carried out moves
  the cursor; `NOTHING` discards what it covered; a model that cannot be reached, or a decision
  that cannot be delivered, ends `SKIPPED` with everything kept.
- `bridge.py`: `WardenPatrolPort`, the real Warden's port (scan and fence the facts, run the
  episode, send the Alarm, Nectar or Wax proposal, record `warden.patrol`), and `last_patrols`.
- `tick.py`: `watch_tick`, the one call the Warden makes.

## What reaches the trail and the Queen

Nothing the machine said. The Alarm and the Wax proposal describe themselves by counts and times;
`warden.patrol` carries an outcome and two counts; `memory.bee_bread_deposited` carries a kind.
The words live only in the `C2` observations, the `C2` episode record's reason, and the `C2` Nectar
and Wax text.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/wardens/watch -q
uv run --frozen pytest packages/hivemind/tests/e2e/test_watch_and_patrol.py -q
```
