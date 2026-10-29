"""live_llm / local_llm variants of the handoff eval: a real provider resumes from a real Handoff.

Gated exactly like `tests.contracts.test_llm_provider_contract`'s own `live_llm` suite: skips
cleanly unless `HIVEMIND_LIVE_LLM=1` is set, plus the credentials/endpoint the chosen manifest
needs. The provider itself is resolved from `docs/manifests/minimal.toml` (hosted, `live_llm`) or
`docs/manifests/local.toml` (a local OpenAI-compatible server, `local_llm`, pointed at
`HIVEMIND_LOCAL_LLM_BASE_URL` and, if set, `HIVEMIND_LOCAL_WORKER_MODEL`) through
`hivemind.cli.stores.build_registry` -- never a literal model id or base URL in this file
(`scripts/check_no_model_ids.py`).

Both variants now assert no-redo strictly, alongside completion and Handoff shape:
`hivemind.workers.roles.drone.sources.DroneSources.handoff` surfaces the whole resumed Handoff --
`do_not_redo` rendered as an explicit "Already done, do not repeat" instruction list included --
into the resuming bee's own prompt (defect 2 this dispatch fixed; see `tests.evals.handoff.
scenario.run_live_handoff_scenario`'s own docstring), so a real model has the same textual signal
the fake scenario's own scripting only used to approximate.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/roadmap.md step 4.5 for "Run with the fake in CI and with real providers under
      live_llm."
    - tests.contracts.test_llm_provider_contract for the same environment-gate pattern.
    - tests.evals.handoff.scenario for run_live_handoff_scenario.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from builders.evals import local_eval_manifest
from evals.handoff.grader import build_report
from evals.handoff.scenario import run_live_handoff_scenario

from hivemind.cli.stores import build_registry
from hivemind.forage.slots import ModelSlot
from hivemind.llm.slots import BoundModel
from hivemind.manifest import HiveManifest, load_manifest
from waggle.clock import SystemClock

# packages/hivemind/tests/evals/handoff/test_handoff_eval_live.py -> parents[5] is the repo root,
# matching tests/unit/cli/test_stores.py's own climb.
_REPO_ROOT = Path(__file__).resolve().parents[5]
_MANIFESTS_DIR = _REPO_ROOT / "docs" / "manifests"


def _worker(manifest: HiveManifest) -> BoundModel:
    """Resolve `manifest`'s WORKER binding the way the Hive does."""
    return build_registry(manifest, os.environ, SystemClock()).bound(ModelSlot.WORKER)


@pytest.mark.live_llm
async def test_a_fresh_bee_resumes_from_a_handoff_with_a_real_hosted_provider() -> None:
    """Skips cleanly unless HIVEMIND_LIVE_LLM=1 and an Anthropic key are set."""
    if os.environ.get("HIVEMIND_LIVE_LLM") != "1":
        pytest.skip("HIVEMIND_LIVE_LLM is not set to '1'.")
    if not os.environ.get("HIVEMIND_ANTHROPIC_API_KEY"):
        pytest.skip("HIVEMIND_ANTHROPIC_API_KEY must be set.")

    result = await run_live_handoff_scenario(
        _worker(load_manifest(_MANIFESTS_DIR / "minimal.toml"))
    )

    report = build_report(
        scenario="live-hosted",
        expected_files=result.expected_files,
        present_files=result.present_files,
        handoff=result.handoff,
        second_bee_calls=result.second_bee_writes,
    )
    assert report.completion.passed, report.completion
    assert report.handoff_shape.passed, report.handoff_shape
    assert report.no_redo.passed, report.no_redo


@pytest.mark.local_llm
async def test_a_fresh_bee_resumes_from_a_handoff_with_a_local_provider() -> None:
    """Skips cleanly unless HIVEMIND_LIVE_LLM=1 and a local server base URL are set."""
    if os.environ.get("HIVEMIND_LIVE_LLM") != "1":
        pytest.skip("HIVEMIND_LIVE_LLM is not set to '1'.")
    if not os.environ.get("HIVEMIND_LOCAL_LLM_BASE_URL"):
        pytest.skip("HIVEMIND_LOCAL_LLM_BASE_URL must be set.")

    result = await run_live_handoff_scenario(_worker(local_eval_manifest(os.environ)))

    report = build_report(
        scenario="local",
        expected_files=result.expected_files,
        present_files=result.present_files,
        handoff=result.handoff,
        second_bee_calls=result.second_bee_writes,
    )
    assert report.completion.passed, report.completion
    assert report.handoff_shape.passed, report.handoff_shape
    assert report.no_redo.passed, report.no_redo
