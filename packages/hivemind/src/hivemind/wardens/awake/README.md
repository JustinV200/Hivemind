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

## Public API (roadmap step 11.10)

- `decide_patrol` (`patrol.py`): a watching Warden's Patrol as one stateless episode. It is
  `decide_awake`'s sibling for a different question: the event is the Patrol (a trusted framing
  with the pre-filter's counts, and the signals and observations as a fenced block of untrusted
  facts, scanned like any outside text), the prompt is `PromptName.PATROL`, the principal reads
  `C2` hot state (only this Warden's own earlier Patrol decisions, `PatrolSources`), and the answer
  is a `hivemind.wardens.watch.PatrolDecision`: ALARM, NECTAR, WAX or NOTHING. The episode record
  is `C2` and keeps the framing but not the fenced facts. A model that cannot be reached raises
  an `LLMError`, which the Patrol (not this episode) turns into a skipped review.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/wardens/awake -q
```
