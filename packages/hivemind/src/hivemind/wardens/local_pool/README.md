# hivemind.wardens.local_pool

The local_pool package is the Warden's local model pool: allocating its Cell's resources to
model servers it hosts itself, so a Nuc (a colonized Real Cell with its own model server) keeps
working while disconnected.

## Public API (roadmap step 3.19)

- `LocalPool` (`pool.py`): a bare sub-bee-slot counter against a grant's `max_sub_bees`.
  `acquire()`/`release()`/`resize()`; the Warden's own local-model-server allocation (VRAM, disk,
  seats) is `hosting.py`, added in phase 8.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/wardens/local_pool -q
```
