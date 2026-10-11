# hivemind.wardens.spawn

The spawn package is how a Warden starts a new Worker on its Cell and hands it the CellSession
it needs.

## Public API (roadmap step 3.19)

- `SubBee` (`sub_bee.py`): the Warden's own bookkeeping row for one spawned Worker -- its
  mirrored `WorkerState`, its current binding, its last Handoff and its owned runtime task/link;
  `has_ended` says the row can go, and `awaits_successor` that it stopped at a Handoff its own
  Warden ordered, so a fresh bee resumes the task from it.
- `WardenCellContext`, `spawn_sub_bee` (`spawn.py`): attenuate capabilities, carve a grant slice,
  resolve the assignment's slot to a live model, build the sub-bee's own `CappingGate` and
  `WorkerContext`, start its `WorkerRuntime`, and send it its first `TaskAssign`.
- `sub_bee_capabilities`, `declared_write_roots` (`attenuate.py`, roadmap step 10.3): one sub-bee's
  slice, read off its `TaskAssign` -- the role default, the task's network scopes and the goal's
  set, narrowed by `hivemind.workers.worker_capabilities`.
- `BindingCheck`, `authorize_binding` (`binding.py`, roadmap step 10.3): the `slot_binding` point;
  `spawn_sub_bee` raises `BindingRefusedError` when it refuses.

## Routing (roadmap step 8.1, ADR-0045)

- `Routed`, `RouteScope`, `route_rebind`, `BindingTarget` (`route.py`): the Warden's routing step.
  `spawn_sub_bee` routes the sub-bee's first binding before the Guard looks: it builds
  `hivemind.llm.routing.RoutingInputs` from the assignment (its slot, its tempo, `vision`/`audio`
  when its Exoskeleton needs a screen, browser screenshots or sound), `WardenDeps.map`,
  `.bindings`, `.routing_rules` and `.local_providers`, the Warden's own Cell and the hosting plan
  the Queen last sent (`WardenCellContext.hosting`), and calls the pure `choose_binding`.
- The Guard's `slot_binding` point checks the routed key, so a refused routed key still raises
  `BindingRefusedError` with nothing started. An explicit string `binding_override` (a Queen-ordered
  rebind) skips routing as before.
- The sub-bee's `BoundModel` chain follows `decision.order`: each key is resolved with
  `WardenDeps.rebind`, every link's own `fallback` is replaced so no manifest-order link leaks in,
  and the routed effort is applied to the head link.
- A choice other than the slot's own key records one `llm.fallback` (slot, provider, `from_key`,
  `to_key`, the rendered reason, `passed_over`, task and goal id). A seat shortfall files one
  `SHARED_SEATS` `ForageRequest` on that source and never waits for the reply: the Fanner still
  makes the call on the chosen binding.
- A rebind on escalation (`wardens/ticks/alarms.py`) calls `route_rebind`: the first routing's
  grade floor plus the assignment's attempt number, so each escalation asks for a stronger model;
  it yields nothing when the failed binding is still the best, and the caller then falls back to
  the grant's next allowed binding or escalates.
- With no `WardenDeps.map` or no `routing_rules` none of this runs: the slot's own key is bound
  and nothing is recorded, exactly as before phase 8.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/wardens/spawn -q
```
