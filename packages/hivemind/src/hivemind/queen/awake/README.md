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

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/queen/awake -q
```
