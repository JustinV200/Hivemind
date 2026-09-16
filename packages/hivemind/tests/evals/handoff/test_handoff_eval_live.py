"""live_llm / local_llm variants of the handoff eval: a real provider resumes from a real Handoff.

Gated exactly like `tests.contracts.test_llm_provider_contract`'s own `live_llm` suite: skips
cleanly unless `HIVEMIND_LIVE_LLM=1` is set, plus the credentials/endpoint the chosen manifest
needs. The provider itself is resolved from `docs/manifests/minimal.toml` (hosted, `live_llm`) or
`docs/manifests/local.toml` (a local OpenAI-compatible server, `local_llm`) through
`hivemind.cli.stores.build_registry` -- never a literal model id or base URL in this file
(`scripts/check_no_model_ids.py`).

Neither variant hard-asserts the no-redo grade. `tests.evals.handoff.scenario.
run_live_handoff_scenario`'s own docstring names why: `hivemind.workers.roles.drone.sources.
DroneSources` never surfaces a resumed Handoff's `do_not_redo` (or `goal`, `progress`,
`next_steps`, `tried_and_failed`, `constraints`, `open_threads`, `pinned_facts`, `notes`) into the
resuming bee's own prompt at all -- only `decisions` reaches it. A real model therefore has no
textual "do not redo this" signal today beyond whatever it infers from `decisions`' own tool-call
history; asserting no-redo here would be asserting a real production gap holds by luck. This
dispatch reports the gap (see this package's own eval report and the dispatch's final summary)
rather than working around it in `src/`, and grades completion and Handoff shape strictly instead,
since neither depends on that seam.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/roadmap.md step 4.5 for "Run with the fake in CI and with real providers under
      live_llm."
    - tests.contracts.test_llm_provider_contract for the same environment-gate pattern.
    - tests.evals.handoff.scenario for run_live_handoff_scenario and its own gap note.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from evals.handoff.grader import build_report
from evals.handoff.scenario import run_live_handoff_scenario

# packages/hivemind/tests/evals/handoff/test_handoff_eval_live.py -> parents[5] is the repo root,
# matching tests/unit/cli/test_stores.py's own climb.
_REPO_ROOT = Path(__file__).resolve().parents[5]
_MANIFESTS_DIR = _REPO_ROOT / "docs" / "manifests"


@pytest.mark.live_llm
async def test_a_fresh_bee_resumes_from_a_handoff_with_a_real_hosted_provider() -> None:
    """Skips cleanly unless HIVEMIND_LIVE_LLM=1 and an Anthropic key are set."""
    if os.environ.get("HIVEMIND_LIVE_LLM") != "1":
        pytest.skip("HIVEMIND_LIVE_LLM is not set to '1'.")
    if not os.environ.get("HIVEMIND_ANTHROPIC_API_KEY"):
        pytest.skip("HIVEMIND_ANTHROPIC_API_KEY must be set.")

    result = await run_live_handoff_scenario(_MANIFESTS_DIR / "minimal.toml", os.environ)

    report = build_report(
        scenario="live-hosted",
        expected_files=result.expected_files,
        present_files=result.present_files,
        handoff=result.handoff,
        second_bee_calls=result.second_bee_writes,
    )
    assert report.completion.passed, report.completion
    assert report.handoff_shape.passed, report.handoff_shape
    # no_redo is reported, not asserted -- see the module docstring's own seam note.


@pytest.mark.local_llm
async def test_a_fresh_bee_resumes_from_a_handoff_with_a_local_provider() -> None:
    """Skips cleanly unless HIVEMIND_LIVE_LLM=1 and a local server base URL are set."""
    if os.environ.get("HIVEMIND_LIVE_LLM") != "1":
        pytest.skip("HIVEMIND_LIVE_LLM is not set to '1'.")
    if not os.environ.get("HIVEMIND_LOCAL_LLM_BASE_URL"):
        pytest.skip("HIVEMIND_LOCAL_LLM_BASE_URL must be set.")

    result = await run_live_handoff_scenario(_MANIFESTS_DIR / "local.toml", os.environ)

    report = build_report(
        scenario="local",
        expected_files=result.expected_files,
        present_files=result.present_files,
        handoff=result.handoff,
        second_bee_calls=result.second_bee_writes,
    )
    assert report.completion.passed, report.completion
    assert report.handoff_shape.passed, report.handoff_shape
    # no_redo is reported, not asserted -- see the module docstring's own seam note.
