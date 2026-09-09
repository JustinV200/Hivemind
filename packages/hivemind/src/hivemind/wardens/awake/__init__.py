"""Provide the Warden's Awake mode: a bounded, stateless episode where it may think with a model.

Each episode is assembled from state rather than accumulated as a running conversation
(codingrules section 8.8). `WardenDecision` (`decision.py`) is the one structured value an episode
ever produces -- a `hivemind.wardens.autopilot.WardenAction`, a reason, and (for `REBIND`) a
binding; `decide_awake` (`episode.py`) is the episode itself: assemble hot state, render the
`warden_system` prompt, ask the model through the degradation ladder, record the decision as an
`EpisodeRecord`, and return it, with nothing kept afterwards.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package.
    Handles the Warden's bounded, stateless episode where it may think with a model. Called by
    `hivemind.wardens.warden.Warden`'s tick whenever autopilot returns `NEEDS_JUDGEMENT`.

Key invariants:
    - `decide_awake` keeps no state across calls (codingrules section 8.8: "Awake episodes are
      stateless").

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under wardens.
    - .claude/codingrules.md section 8.8 for the awake-episode shape this package implements.
    - .claude/roadmap.md phase 3 step 3.19 for the work that first populates it.

Public API (roadmap step 3.19):
    - MAX_BINDING_CHARS, MAX_REASON_CHARS, WardenDecision: the one structured decision (decision).
    - AWAKE_MAX_OUTPUT_TOKENS, AWAKE_OUTPUT_RESERVE_TOKENS, WARDEN_BUDGET_FRACTION, decide_awake:
      the episode itself (episode).
"""

from hivemind.wardens.awake.decision import MAX_BINDING_CHARS, MAX_REASON_CHARS, WardenDecision
from hivemind.wardens.awake.episode import (
    AWAKE_MAX_OUTPUT_TOKENS,
    AWAKE_OUTPUT_RESERVE_TOKENS,
    WARDEN_BUDGET_FRACTION,
    decide_awake,
)

__all__ = [
    "AWAKE_MAX_OUTPUT_TOKENS",
    "AWAKE_OUTPUT_RESERVE_TOKENS",
    "MAX_BINDING_CHARS",
    "MAX_REASON_CHARS",
    "WARDEN_BUDGET_FRACTION",
    "WardenDecision",
    "decide_awake",
]
