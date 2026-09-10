"""Provide the Queen's Awake mode: a bounded, stateless episode where she may think with a model.

Each episode is assembled from state rather than accumulated as a running conversation
(codingrules section 8.8). `QueenDecision` (`decision.py`) is the one structured value an episode
ever produces -- a `hivemind.queen.autopilot.QueenAction`, the task it concerns, a reason, and (for
`REBIND`) a binding; `QueenSources`/`decide_awake` (`episode.py`) are the episode itself: adapt the
Queen's own chamber, memory store and human inbox into `hivemind.memory.HotStateSources`, assemble
hot state, render the `queen_system` prompt, ask the model through the degradation ladder, record
the decision as an `EpisodeRecord`, and return it, with nothing kept afterwards.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package.
    Handles the Queen's bounded, stateless episode where she may think with a model. Called by
    `hivemind.queen.queen.Queen`'s tick whenever autopilot returns `NEEDS_JUDGEMENT`.

Key invariants:
    - `decide_awake` keeps no state across calls (codingrules section 8.8: "Awake episodes are
      stateless").

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under queen.
    - .claude/codingrules.md section 8.8 for the awake-episode shape this package implements.
    - .claude/roadmap.md phase 3 step 3.20 for the work that first populates it.

Public API (roadmap step 3.20):
    - MAX_BINDING_CHARS, MAX_REASON_CHARS, MAX_TASK_ID_CHARS, QueenDecision: the one structured
      decision (decision).
    - ACTIVE_TASKS_LIMIT, AWAKE_MAX_OUTPUT_TOKENS, AWAKE_OUTPUT_RESERVE_TOKENS, NOTES_LIMIT,
      RECENT_DECISIONS_LIMIT, QueenSources, decide_awake: the episode itself (episode).
"""

from hivemind.queen.awake.decision import (
    MAX_BINDING_CHARS,
    MAX_REASON_CHARS,
    MAX_TASK_ID_CHARS,
    QueenDecision,
)
from hivemind.queen.awake.episode import (
    ACTIVE_TASKS_LIMIT,
    AWAKE_MAX_OUTPUT_TOKENS,
    AWAKE_OUTPUT_RESERVE_TOKENS,
    NOTES_LIMIT,
    RECENT_DECISIONS_LIMIT,
    QueenSources,
    decide_awake,
)

__all__ = [
    "ACTIVE_TASKS_LIMIT",
    "AWAKE_MAX_OUTPUT_TOKENS",
    "AWAKE_OUTPUT_RESERVE_TOKENS",
    "MAX_BINDING_CHARS",
    "MAX_REASON_CHARS",
    "MAX_TASK_ID_CHARS",
    "NOTES_LIMIT",
    "RECENT_DECISIONS_LIMIT",
    "QueenDecision",
    "QueenSources",
    "decide_awake",
]
