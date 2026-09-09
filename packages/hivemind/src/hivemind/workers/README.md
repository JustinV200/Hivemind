# hivemind.workers

The workers package is the Worker runtime: the roles that do the Hive's actual work (Forager,
Scout, GuardBee, Undertaker, Drone, HouseBee), the tools they call, and the tactics they can
invoke, such as writing in a more human style under the Pheromone Mask. Roadmap step 3.15 builds
the runtime every role shares; step 3.16 adds the first role (the Drone) and its built-in tools.

## Modules

- `base.py` -- `Worker`, the one Protocol every role implements, and `WorkerOutcome`, what its
  `run` returns: a claim of completion (`claimed=True`) or a request to hand off
  (`handoff` set) -- never both, never neither. A Worker never marks itself `SUCCEEDED`.
- `state.py` -- `WorkerState` and its one transition table (codingrules Appendix C's "Worker"
  row): `SPAWNED -> RUNNING -> DONE/FAILED/KILLED`, `RUNNING <-> HANDING_OFF`,
  `RUNNING <-> PAUSED`. Mirrors `waggle.messages.supervision.WorkerState` member for member.
- `context.py` -- `WorkerContext` (everything a role may use; never a provider, a subprocess
  handle, or the Cell's kind), `GrantSlice` (one Worker's share of a Warden's `ForageGrant`) and
  `QuestionChannel` (ask a blocking Question, get its Answer). Since roadmap step 3.16,
  `WorkerContext` also carries `capping` (the Capping gate a tool's side effect proposes through),
  `lease` (a `LeaseView` onto the Warden's Real Cell lease) and `call_gate` (the seam every model
  call passes through -- a `FannerLane` or a bare `DirectCallGate`).
- `telemetry.py` -- `TelemetryTracker`: the mutable per-Worker `ContextTelemetry` a role writes
  between turns and the runtime reads for every `Heartbeat`; also the `cancel_requested`/
  `handoff_requested` flags and the `wait_if_paused()` a role's own turn loop cooperates with.
- `capabilities.py` -- `worker_capabilities(warden_caps, needs, scratch_root)`: a Worker's strict
  `CapabilitySet` slice, never wider than its Warden's.
- `errors.py` -- `WorkerError` (root), `InvalidWorkerTransitionError`, `WorkerCancelledError`.
- `runtime/` -- `WorkerRuntime`, the `waggle.loop.TickLoop` that receives `TaskAssign`, runs the
  role, sends `Heartbeat`/`TaskProgress`/`TaskResult`, honours `TaskCancel`/`TaskPause`/
  `TaskResume`/every `Intervene` lever, checkpoints and restarts at a handoff, and raises an
  `AlarmRaised` instead of crashing when the role does. Split internally into `deps.py`
  (`RuntimeDeps`), `mailbox.py` (the transport, the receive-or-heartbeat race, the blocking
  Question channel), `reporter.py` (this Worker's own `WorkerState` and every outgoing message)
  and `attempt.py` (starting, cancelling and interpreting one role attempt); `__init__.py` is the
  package's own face.
- `roles/` -- one module (or package) per Worker role; `hivemind.workers.roles.drone.Drone` is the
  first, added by roadmap step 3.16. See `hivemind.workers.roles`'s own README.
- `tools/` -- the tool implementations a Worker calls while it works, each one going through its
  Cell's `CellSession`. Roadmap step 3.16 adds the Drone's first five: `run_command`, `read_file`,
  `write_file`, `http_request` and `ask`. See `hivemind.workers.tools`'s own README.
- `tactics/` -- empty until a later roadmap step.

## Public API (roadmap 3.15)

See the `Public API:` section of `__init__.py` for the full, current list; the summary above names
each name's home module.

## Public API (roadmap 3.16)

`WorkerContext`'s three new fields (`capping`, `lease`, `call_gate`), and the `roles`/`tools`
sub-packages, are not re-exported at this package's own top level: a Warden (roadmap step 3.19)
imports `hivemind.workers.roles.Drone` and `hivemind.workers.tools.build_registry` directly, the
same way it already imports `hivemind.workers.context.WorkerContext`. See `hivemind.workers.roles`
and `hivemind.workers.tools`'s own `Public API:` sections for their full, current lists.

## How to test this

```
uv run --frozen pytest packages/hivemind/tests/unit/workers
```

`tests/unit/workers/` mirrors this package module for module (`tests/unit/workers/runtime/`
mirrors `runtime/`). `tests/builders/workers.py` provides `make_context` (a valid `WorkerContext`
over fakes), `make_assignment` (a valid `TaskAssign`), `ScriptedWorker` (a `Worker` whose `run`
follows a scripted plan: return an outcome, raise, hand off after N ticks, block on a Question,
observe a pause) and `WardenEnd` (wraps the Warden side of a `waggle.transport.memory.
MemoryTransport` pair to send `TaskAssign`/`TaskCancel`/`TaskPause`/`TaskResume`/`Intervene` and
collect `Heartbeat`/`TaskProgress`/`TaskResult`/`AlarmRaised`/`Question`). Every `WorkerRuntime`
test drives time with `waggle.clock.FakeClock`; no real sleeps.
