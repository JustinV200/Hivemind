"""Provide the Fanner: the seat meter every model call in the Hive passes through (roadmap 3.12a).

Named after the bees that fan their wings to regulate the hive's temperature and airflow
(codingrules 6.1). The Fanner is "the only place seat counts are enforced, so a grant is a fact
rather than a suggestion" (codingrules section 8.10): every call metered by a
`hivemind.llm.fanner.lane.FannerLane` passes through a per-binding seat limit
(`hivemind.llm.fanner.seats.SeatMeter`, queued by tempo) and a per-provider rate limit
(`hivemind.llm.fanner.limiter.ProviderRateLimiter`), measures its own latency and tokens per
second, and updates the Forage map (`hivemind.forage.map.ForageMap`) so the next call's routing
sees fresh figures. It also implements spill-over: moving a call to the next binding in its
fallback chain when the current one's grade is below the calling tempo's floor, the model is not
loaded there, or seat-queueing has eaten too much of the latency budget
(`hivemind.llm.fanner.spill`). Every completed call and every spill is recorded on the Pheromone
Trail through an injected `LlmEventRecorder` (`hivemind.llm.fanner.recorder`), never written by
hand.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm`. `FannerLane`
    implements `hivemind.llm.ladders.gate.CallGate` exactly, so a degradation ladder
    (`complete_structured`, `run_tool_loop`) can take a lane as its `gate=` with no code of its
    own aware the Fanner exists. Calls into `hivemind.forage` and `hivemind.llm`'s own boundary;
    nothing under an `autopilot/` directory may import this package (codingrules section 4), and
    nothing here needs to, since autopilot never makes a model call.

Key invariants:
    - The Fanner never refuses a call: with no fallback binding left, `FannerLane.complete` makes
      the call on the current binding regardless of any spill reason it found.
    - Whatever is not re-exported here is private to this package's own internal wiring
      (codingrules 5.4); each submodule's own `__all__` still names a wider surface its own test
      module imports directly (codingrules 14.2), matching `hivemind.llm.ladders`' convention.

See Also:
    - .claude/codingrules.md section 8.10 for the Fanner's role, spill-over and the two pools.
    - .claude/roadmap.md step 3.12a for this package's own roadmap bullet, verbatim.
    - docs/adr/0009-structured-output-and-tool-call-degradation-ladders.md for the CallGate seam
      this package's FannerLane implements.
    - docs/adr/0015-forage-map-seats-footprints-and-the-fanner.md for the map/Fanner split this
      package is the "meters" half of.
    - hivemind.llm.ladders.gate for CallGate, the Protocol FannerLane implements.

Public API:
    - The Fanner and its lanes (`hivemind.llm.fanner.lane`): `Fanner`, `FannerDeps`, `FannerLane`,
      `DEFAULT_SEATS`, `LLM_CALL_KIND`, `LLM_SPILL_KIND`.
    - Spill-over (`hivemind.llm.fanner.spill`): `SpillReason`, `SPILL_WAIT_FRACTION`.
    - Rate limiting (`hivemind.llm.fanner.limiter`): `RateLimit`.
    - Trail recording (`hivemind.llm.fanner.recorder`): `LlmEventRecorder`, `NullLlmEventRecorder`,
      `TrailLlmEventRecorder`.
"""

from hivemind.llm.fanner.lane import (
    DEFAULT_SEATS,
    LLM_CALL_KIND,
    LLM_SPILL_KIND,
    Fanner,
    FannerDeps,
    FannerLane,
)
from hivemind.llm.fanner.limiter import RateLimit
from hivemind.llm.fanner.recorder import (
    LlmEventRecorder,
    NullLlmEventRecorder,
    TrailLlmEventRecorder,
)
from hivemind.llm.fanner.spill import SPILL_WAIT_FRACTION, SpillReason

__all__ = [
    "DEFAULT_SEATS",
    "LLM_CALL_KIND",
    "LLM_SPILL_KIND",
    "SPILL_WAIT_FRACTION",
    "Fanner",
    "FannerDeps",
    "FannerLane",
    "LlmEventRecorder",
    "NullLlmEventRecorder",
    "RateLimit",
    "SpillReason",
    "TrailLlmEventRecorder",
]
