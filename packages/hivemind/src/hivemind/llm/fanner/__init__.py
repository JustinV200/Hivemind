"""Provide the Fanner: the seat meter every model call in the Hive passes through (roadmap 3.12a).

Named after the bees that fan their wings to regulate the hive's temperature and airflow
(codingrules 6.1). The Fanner is "the only place seat counts are enforced, so a grant is a fact
rather than a suggestion" (codingrules section 8.10): every call metered by a
`hivemind.llm.fanner.lane.FannerLane` passes through a per-binding seat limit
(`hivemind.llm.fanner.seats.SeatMeter`, queued by tempo) and a per-provider rate limit
(`hivemind.llm.fanner.limiter.ProviderRateLimiter`), measures its own latency, tokens per second
and (roadmap step 4.7a) whatever rate-limit headroom the provider's own response reported, and
updates the Forage map (`hivemind.forage.map.ForageMap`) so the next call's routing sees fresh
figures. It also implements spill-over: moving a call to the next binding in its fallback chain
when the current one's grade is below the calling tempo's floor, the model is not loaded there,
seat-queueing has eaten too much of the latency budget, or the source is currently throttled after
a `RateLimitedError` (`hivemind.llm.fanner.spill`). Every completed call, every spill and every
throttle is recorded on the Pheromone Trail through an injected `LlmEventRecorder`
(`hivemind.llm.fanner.recorder`), never written by hand.

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
      `DEFAULT_SEATS`, `DEFAULT_THROTTLE_S`, `LLM_CALL_KIND`, `LLM_SPILL_KIND`,
      `LLM_THROTTLED_KIND`.
    - Spill-over (`hivemind.llm.fanner.spill`): `SpillReason`, `SPILL_WAIT_FRACTION`.
    - Rate limiting (`hivemind.llm.fanner.limiter`): `RateLimit`.
    - Trail recording (`hivemind.llm.fanner.recorder`): `LlmEventRecorder`, `NullLlmEventRecorder`,
      `TrailLlmEventRecorder`, `CompositeLlmEventRecorder` (roadmap step 4.8's own wiring step:
      fans one occurrence out to several recorders, e.g. a ledger recorder alongside the trail).
    - Metered transcription (`hivemind.llm.fanner.transcription`, roadmap step 6.5a's subset for
      10.5f): `MeteredTranscriber`, a `TranscriptionProvider` whose every call takes a seat,
      spills and records one `llm.call` on TRANSCRIBER; `bind_transcriber(registry, fanner,
      tempo)`, the composition root's one call; `AUDIO_SECONDS_KEY`, the payload key carrying
      the seconds of audio.
"""

from hivemind.llm.fanner.lane import (
    DEFAULT_SEATS,
    DEFAULT_THROTTLE_S,
    LLM_CALL_KIND,
    LLM_SPILL_KIND,
    LLM_THROTTLED_KIND,
    Fanner,
    FannerDeps,
    FannerLane,
)
from hivemind.llm.fanner.limiter import RateLimit
from hivemind.llm.fanner.recorder import (
    CompositeLlmEventRecorder,
    LlmEventRecorder,
    NullLlmEventRecorder,
    TrailLlmEventRecorder,
)
from hivemind.llm.fanner.spill import SPILL_WAIT_FRACTION, SpillReason
from hivemind.llm.fanner.transcription import (
    AUDIO_SECONDS_KEY,
    MeteredTranscriber,
    bind_transcriber,
)

__all__ = [
    "AUDIO_SECONDS_KEY",
    "DEFAULT_SEATS",
    "DEFAULT_THROTTLE_S",
    "LLM_CALL_KIND",
    "LLM_SPILL_KIND",
    "LLM_THROTTLED_KIND",
    "SPILL_WAIT_FRACTION",
    "CompositeLlmEventRecorder",
    "Fanner",
    "FannerDeps",
    "FannerLane",
    "LlmEventRecorder",
    "MeteredTranscriber",
    "NullLlmEventRecorder",
    "RateLimit",
    "SpillReason",
    "TrailLlmEventRecorder",
    "bind_transcriber",
]
