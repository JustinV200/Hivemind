# hivemind.wardens.awake

The awake package is the Warden's Awake mode: a bounded, stateless episode where the Warden is
allowed to think with a model, assembled from state rather than accumulated as a running
conversation.

## Public API (roadmap step 3.19)

- `WardenDecision` (`decision.py`): the one structured value an episode produces -- a
  `WardenAction`, a reason, and (for `REBIND`) a target binding.
- `decide_awake` (`episode.py`): assembles hot state (`hivemind.memory.assemble`), renders
  `warden_system.md`, calls `hivemind.llm.complete_structured` through the Warden's own
  `call_gate`, records the decision as an `EpisodeRecord`, and returns it.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/wardens/awake -q
```
