"""local_llm: offline mode on a real local model server, with the no-egress guard.

The scripted e2e suite (`tests/e2e/offline/`) proves the offline wiring over a scripted OpenAI-
compatible server; this is the same proof with a real model behind the socket, the part only the
`local_llm` job can show. Each case derives its manifest from `docs/manifests/offline.toml`
(`tests/e2e/offline/live.py`: the server, the two chat models, optionally Whisper and an embedding
model, all from the environment) and runs under the socket guard, so a connect to anything but
loopback fails the test. Quality is deliberately not asserted (a small model may fail a goal, and
`hive llm eval` is what grades that): each case asserts that the goal reached a verdict without
timing out, that every `llm.call` named a provider that runs on this machine, and that the case's
own claim holds.

1. The haiku goal on the Hive Stand, the Queen, Drone and Judge on the real server.
2. A spoken goal: a WAV clip transcribed by Whisper in this process (`whisper_local`, the
   `hivemind[whisper]` extra, a model already in the local cache) and then run as the goal.
3. The Forager (browsing Worker) with the real model as WORKER and the offline file's
   `vision = false`: only the Worker is real here; a scripted loopback server plans and judges, so
   the plan is exact and what is under test is the model driving the browser through the page's
   accessibility tree, with no screenshot tool offered.

Gated like `tests.evals.honey.test_ripening_local`: skips cleanly unless `HIVEMIND_LIVE_LLM=1` and
`HIVEMIND_LOCAL_LLM_BASE_URL` are set. `HIVEMIND_LOCAL_LARGE_MODEL` and `HIVEMIND_LOCAL_SMALL_MODEL`
replace the shipped file's two chat model ids; `HIVEMIND_LOCAL_WHISPER_MODEL` the Whisper size;
`HIVEMIND_LOCAL_SPEECH_CLIP` is a WAV of someone saying a goal (case 2 skips without one);
`HIVEMIND_LOCAL_GOAL_TIMEOUT_S` (default 900) bounds each goal. No model id or URL is written here
(`scripts/check_no_model_ids.py`).

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - Whisper runs with `local_files_only` (offline mode forces it): the model is fetched before the
      run, never during it.

See Also:
    - tests/e2e/offline/ for the scripted counterparts of all three cases.
    - tests/evals/README.md for how to run them.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
from pathlib import Path

import pytest
from e2e.offline import (
    GOAL,
    EgressLog,
    Outcome,
    assert_every_call_local,
    calls_on,
    run_goal_in_process,
)
from e2e.offline.live import live_offline_manifest
from e2e.phase6_exit_helpers import browser_missing, login_plan, serve_login_site
from e2e.scripted_openai import Scenario, serve_in_thread

from hivemind.cell import HoneyClearance
from hivemind.cli.compose import Hive, build_hive, run_goal, run_hive
from hivemind.forage import Tempo
from hivemind.forage.slots import ModelSlot
from hivemind.llm import AUDIO_SECONDS_KEY, AudioClip, FannerTranscriptionGate
from hivemind.pheromone import MAX_QUERY_LIMIT, PheromoneEvent, TrailQuery
from waggle.clock import SystemClock

_DEFAULT_TIMEOUT_S = (
    900.0  # A small model on a CPU runner is slow; the Queen's plan alone is minutes.
)
_FULL_ACCESS = {"hive_stand": {"access_level": "FULL"}}  # The browser Exoskeleton's access level.


def _live_environ() -> dict[str, str]:
    """The process environment, or a skip unless the local server is named."""
    if os.environ.get("HIVEMIND_LIVE_LLM") != "1":
        pytest.skip("HIVEMIND_LIVE_LLM is not set to '1'.")
    if not os.environ.get("HIVEMIND_LOCAL_LLM_BASE_URL"):
        pytest.skip("HIVEMIND_LOCAL_LLM_BASE_URL must be set.")
    return dict(os.environ)


def _timeout_s(environ: dict[str, str]) -> float:
    """How long one goal may take, planning included."""
    return float(environ.get("HIVEMIND_LOCAL_GOAL_TIMEOUT_S", _DEFAULT_TIMEOUT_S))


def _assert_ran_to_a_verdict(outcome: Outcome, egress: EgressLog) -> None:
    """The goal did not time out, the Worker made calls, and the guard saw only loopback traffic."""
    assert not outcome.timed_out, "the goal outlasted its timeout"
    assert calls_on(outcome.events, ModelSlot.WORKER), "the Worker made no model call"
    egress.assert_clean_and_live()


@pytest.mark.local_llm
def test_the_haiku_goal_runs_offline_on_a_real_local_model(
    tmp_path: Path, egress: EgressLog
) -> None:
    environ = _live_environ()
    manifest, _path = live_offline_manifest(tmp_path, environ)

    outcome = run_goal_in_process(manifest, GOAL, _timeout_s(environ))

    assert_every_call_local(outcome.events, manifest)
    _assert_ran_to_a_verdict(outcome, egress)


@pytest.mark.local_llm
def test_a_spoken_goal_is_heard_by_in_process_whisper_and_runs(
    tmp_path: Path, egress: EgressLog
) -> None:
    environ = _live_environ()
    if not environ.get("HIVEMIND_LOCAL_SPEECH_CLIP"):
        pytest.skip("HIVEMIND_LOCAL_SPEECH_CLIP must name a WAV of someone saying a goal.")
    if importlib.util.find_spec("faster_whisper") is None:
        pytest.skip("the optional whisper extra (faster-whisper) is not installed.")
    manifest, _path = live_offline_manifest(tmp_path, environ)
    hive = build_hive(manifest, environ={}, clock=SystemClock())
    clip = AudioClip.from_wav(Path(environ["HIVEMIND_LOCAL_SPEECH_CLIP"]).read_bytes())

    heard, timed_out, events = asyncio.run(_hear_and_run(hive, clip, _timeout_s(environ)))

    assert heard.strip(), "Whisper heard nothing in the clip"
    [call] = calls_on(events, ModelSlot.TRANSCRIBER)
    assert call.payload[AUDIO_SECONDS_KEY] == pytest.approx(clip.duration_s)
    assert_every_call_local(events, manifest)
    assert not timed_out
    egress.assert_clean_and_live()


async def _hear_and_run(
    hive: Hive, clip: AudioClip, timeout_s: float
) -> tuple[str, bool, tuple[PheromoneEvent, ...]]:
    """Transcribe `clip` through the Fanner's gate, then run what was heard as the goal."""
    async with run_hive(hive):
        gate = FannerTranscriptionGate(hive.fanner, Tempo())
        transcript = await gate.transcribe(hive.registry.bound_transcriber(), clip)
        report = await run_goal(
            hive, transcript.text, clearance=HoneyClearance.C1, timeout_s=timeout_s
        )
    events = await hive.stores.trail.query(TrailQuery(limit=MAX_QUERY_LIMIT))
    return transcript.text, report.timed_out, tuple(events)


@pytest.mark.local_llm
def test_the_forager_reads_the_page_by_its_accessibility_tree_on_a_real_local_model(
    tmp_path: Path, egress: EgressLog
) -> None:
    environ = _live_environ()
    if reason := browser_missing():
        pytest.skip(reason)
    with serve_login_site() as origin:
        scenario = Scenario(plan_json=login_plan(origin, browser_only=True))
        with serve_in_thread(scenario) as planner_url:
            manifest, _path = live_offline_manifest(tmp_path, environ, _FULL_ACCESS, planner_url)
            outcome = run_goal_in_process(manifest, GOAL, _timeout_s(environ))

    assert_every_call_local(outcome.events, manifest)
    # The offline file declares `vision = false`, so the real Worker was offered no screenshot.
    assert manifest.llm.providers["local"].capabilities.vision is False
    _assert_ran_to_a_verdict(outcome, egress)
