"""End-to-end: a goal spoken as a clip is transcribed on the local TRANSCRIBER and then runs.

Phase 8's spoken-goal criterion under `offline.toml`. The derived manifest binds the TRANSCRIBER
slot to an `openai_compat` provider on the scripted loopback server's `/v1/audio/transcriptions`,
the endpoint a local speech server (or a Whisper behind an OpenAI-compatible wrapper) exposes. A
WAV clip carries a planted byte run the scenario maps to the words of a goal (the mechanics of
`test_voice_on_hive_serve`); the clip goes through the Fanner's transcription gate exactly as the
Entrance's voice route sends one, and the words it returns are submitted as the goal. Asserted: the
transcript is the scripted words, the trail holds one `llm.call` on TRANSCRIBER carrying the clip's
audio seconds, the goal succeeds, every `llm.call` is local, no socket left loopback, and the
planted bytes are on no trail payload. The in-process Whisper counterpart is `local_llm`-gated in
`tests/evals/offline/`, where a real model and a real clip exist.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - The clip goes through `FannerTranscriptionGate`, not the provider directly, so the call is
      metered and recorded as it is in a running Hive.

See Also:
    - tests/e2e/test_voice_on_hive_serve.py for the spoken goal over `hive serve`.
    - hivemind.llm.fanner.transcription for the gate.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from builders.audio import marked_wav
from e2e.offline import (
    HAIKU_FILES,
    EgressLog,
    assert_every_call_local,
    calls_on,
    offline_manifest,
)
from e2e.offline.rig import TIMEOUT_S
from e2e.scripted_openai import Scenario, serve_in_thread

from hivemind.cell import HoneyClearance
from hivemind.cli.compose import Hive, build_hive, run_goal, run_hive
from hivemind.forage import Tempo
from hivemind.forage.slots import ModelSlot
from hivemind.llm import AUDIO_SECONDS_KEY, AudioClip, FannerTranscriptionGate
from hivemind.pheromone import MAX_QUERY_LIMIT, PheromoneEvent, TrailQuery
from waggle.clock import SystemClock

pytestmark = pytest.mark.e2e

_GOAL_WORDS = "Write a haiku about bees in the lavender."  # What the goal clip is heard as.
_MARK = b"OFFLINE-GOAL-4T8Q-HIVE"  # Planted in the clip's samples.


def test_a_spoken_goal_is_transcribed_on_the_local_transcriber_and_runs(
    tmp_path: Path, egress: EgressLog
) -> None:
    scenario = Scenario(
        files=HAIKU_FILES,
        transcript="(no marker: not the clip this test sends)",
        heard=((_MARK, _GOAL_WORDS),),
    )
    with serve_in_thread(scenario) as base_url:
        manifest, _path = offline_manifest(tmp_path, base_url)
        hive = build_hive(manifest, environ={}, clock=SystemClock())

        heard, succeeded, events = asyncio.run(_hear_and_run(hive))

    assert heard == _GOAL_WORDS
    assert succeeded
    [call] = calls_on(events, ModelSlot.TRANSCRIBER)
    assert call.payload[AUDIO_SECONDS_KEY] == 1.0
    assert_every_call_local(events, manifest)
    # The audio itself is nowhere on the trail: only its length is.
    trail_text = json.dumps([event.model_dump(mode="json") for event in events])
    assert _MARK.decode() not in trail_text
    egress.assert_clean_and_live()


async def _hear_and_run(hive: Hive) -> tuple[str, bool, tuple[PheromoneEvent, ...]]:
    """Transcribe the clip through the Fanner's gate, then run the words as the goal."""
    async with run_hive(hive):
        gate = FannerTranscriptionGate(hive.fanner, Tempo())
        clip = AudioClip.from_wav(marked_wav(_MARK))
        transcript = await gate.transcribe(hive.registry.bound_transcriber(), clip)
        report = await run_goal(
            hive, transcript.text, clearance=HoneyClearance.C1, timeout_s=TIMEOUT_S
        )
    events = await hive.stores.trail.query(TrailQuery(limit=MAX_QUERY_LIMIT))
    return transcript.text, report.succeeded, tuple(events)
