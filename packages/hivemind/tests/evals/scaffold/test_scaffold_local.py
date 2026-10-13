"""local_llm: a model on a local server writes the tool the Comb promotes and a Worker then uses.

Roadmap phase 9's fourth exit criterion: "The scaffold scenario passes with SCAFFOLDER local in the
local_llm job." The scenario (`scenario.py`) is the first exit criterion's, cut to a Drone: a
Worker asks for a line counter, the SCAFFOLDER slot writes it, the real Quarantine Comb (the
development subprocess sandbox) proves it, the Queen promotes it, and the Worker calls it and
finishes with the count. The planner, the Worker and the judge stay on the fake provider, as the
other local evals leave everything but their slots to fakes; only the SCAFFOLDER binding moves to
the OpenAI-compatible server `docs/manifests/local.toml` names.

Two cases. The first runs in CI with a scripted fake SCAFFOLDER, so the scenario, its grader and
the Hive wiring are exercised on every run. The second (`local_llm`) skips cleanly unless
`HIVEMIND_LIVE_LLM=1` and `HIVEMIND_LOCAL_LLM_BASE_URL` are set, exactly as
`tests/evals/honey/test_ripening_local.py` does; `HIVEMIND_LOCAL_SCAFFOLDER_MODEL` replaces the
model id local.toml names. A model that writes a tool the Comb rejects fails the case with the
gate named, which is the finding the eval exists to surface.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests/evals/README.md for how to run the local case.
    - tests/e2e/test_phase9_exit_criteria.py for the same flow with a Forager.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from e2e.royal_jelly_runs import NOTES_LINES, NOTES_NAME, lab_manifest
from evals.scaffold.scenario import (
    ScaffoldGrade,
    grade,
    run_scaffold_scenario,
    with_local_scaffolder,
)

from hivemind.cli.stores import build_forage_map, build_provider_registry
from hivemind.forage.slots import ModelSlot
from hivemind.manifest import load_manifest
from waggle.clock import SystemClock

_EXPECTED_SUMMARY = f"{NOTES_NAME} has {NOTES_LINES} lines."


def test_the_scaffold_scenario_passes_with_a_scripted_scaffolder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = run_scaffold_scenario(tmp_path, monkeypatch)

    assert grade(run) == ScaffoldGrade(
        promoted=True, failing_gate="", summary=_EXPECTED_SUMMARY, succeeded=True
    )


def test_the_local_binding_moves_only_the_scaffolder_slot_and_resolves(tmp_path: Path) -> None:
    manifest = load_manifest(lab_manifest(tmp_path), {})
    environ = {"HIVEMIND_LOCAL_LLM_BASE_URL": "http://127.0.0.1:9/v1"}

    local = with_local_scaffolder(manifest, environ)

    assert {key: row.provider for key, row in local.llm.slots.items()} == {
        key: "local" if key == "scaffolder" else "fake" for key in manifest.llm.slots
    }
    # Resolving is lazy (no connection), so the binding can be checked with no server running.
    clock = SystemClock()
    registry = build_provider_registry(local, {}, clock, build_forage_map(local, clock), None)
    assert registry.bound(ModelSlot.SCAFFOLDER).provider.name == "local"
    assert registry.bound(ModelSlot.WORKER).provider.name == "fake"


@pytest.mark.local_llm
def test_the_scaffold_scenario_passes_with_the_scaffolder_on_a_local_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skips cleanly unless HIVEMIND_LIVE_LLM=1 and a local server base URL are set."""
    if os.environ.get("HIVEMIND_LIVE_LLM") != "1":
        pytest.skip("HIVEMIND_LIVE_LLM is not set to '1'.")
    if not os.environ.get("HIVEMIND_LOCAL_LLM_BASE_URL"):
        pytest.skip("HIVEMIND_LOCAL_LLM_BASE_URL must be set.")

    run = run_scaffold_scenario(tmp_path, monkeypatch, os.environ)

    result = grade(run)
    # The gate is named in the failure: a local model's tool the Comb rejects is the finding.
    assert result.promoted, f"the Comb rejected the local model's tool at {result.failing_gate!r}"
    assert result.succeeded and result.summary == _EXPECTED_SUMMARY, result
