# hivemind.queen.awake

The awake package is the Queen's Awake mode: a bounded, stateless episode where she is allowed
to think with a model, assembled from state rather than accumulated as a running conversation.

## Public API (roadmap step 3.20)

- `QueenDecision` (`decision.py`): the one structured value an episode ever produces -- a
  `hivemind.queen.autopilot.QueenAction`, the task it concerns, a reason, and (for `REBIND`) a
  binding hint.
- `QueenSources`, `decide_awake` (`episode.py`): adapt the Queen's own Brood Chamber, memory store
  and human inbox into `hivemind.memory.HotStateSources`; assemble hot state, render the
  `queen_system` prompt, ask the model through the structured-output degradation ladder, record
  the decision as an `EpisodeRecord`, and return it, with nothing kept afterwards. A test proves
  the fake at `ProviderCapabilities.none()` still yields a decision, on the PROMPTED rung.
  `QueenSources.wax(cells)` (roadmap step 4.2a) returns WRITTEN Cell Wax only for a Cell in
  `cells`, capped per Cell (`hivemind.memory.cell_wax.cap_wax_for_hot_state`, `WAX_CAP_PER_CELL`);
  `decide_awake` takes an optional `cells_in_play` (default empty) threaded straight into
  `AssembleRequest`, so `hivemind.queen.ticks.wax` can pass `{the Cell in question}` when judging
  a proposal and see that Cell's own existing wax in the prompt.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/queen/awake -q
```
