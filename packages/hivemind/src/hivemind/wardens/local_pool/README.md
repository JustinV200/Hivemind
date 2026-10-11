# hivemind.wardens.local_pool

The local_pool package is the Warden's local model pool: allocating its Cell's resources to
model servers it hosts itself, so a Nuc (a colonized Real Cell with its own model server) keeps
working while disconnected.

## Public API (roadmap steps 3.19, 4.7, 8.3)

- `SubBeeSlots` (`sub_bee_slots.py`, renamed from `LocalPool`/`pool.py` in step 4.7 -- codingrules
  6.1 now gives `LocalPool` to `hivemind.forage`): a bare sub-bee-slot counter against a grant's
  `max_sub_bees`. `acquire()`/`release()`/`resize()`.
- `hosting/` (roadmap step 8.3, ADR-0044): the Warden's own model-server half, a package because it
  needs six small modules. `ModelServerHost` (`host.py`) starts the OpenAI-compatible servers the
  manifest declares in `[llm.servers]` through the Warden's `CellSession` on a Cell that reports
  `can_host_model` (branching on the capability, never the Cell's kind), waits for the provider's
  own health probe (`launch.py`), adds each source to the live Forage map with this Cell as host
  and sends the Queen `forage.source_added` (`report.py`), and reverses all of it in the opposite
  order on `stop`, `unload` (a Queen-ordered eviction) or `stop_all` (the Warden's own `stop()`,
  before the lease is released: nothing it started survives). `sources.plan_loads` is the
  ceilings rule, pure: a source loads only when it is in `Ceilings.loadable_sources` and the
  cumulative VRAM stays within `model_vram_bytes` (with no ceilings the manifest is the allowlist).
  `config.ServerConfig` is the decoupled row the Warden reads (it may not import the manifest;
  `hivemind.cli.stores.server_configs` builds it) and `door.provider_door` closes the register and
  unregister callables over the model registry. A server that never turns healthy is stopped and
  recorded dead (`SERVER_DIED`); `sweep()`, on the Warden's heartbeat cadence, withdraws one whose
  process exited on its own. Weights are whatever the server finds on the Cell; shipping them from
  the Basket waits for roadmap 9.2a.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/wardens/local_pool -q
```

The whole-Warden behaviour is `tests/unit/wardens/test_warden_hosting.py`; the real-process proof
is `tests/e2e/test_model_server_on_hive_stand.py`.
