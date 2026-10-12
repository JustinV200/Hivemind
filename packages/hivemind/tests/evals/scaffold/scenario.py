"""Run the scaffold scenario: a Worker asks for a tool and a model writes it, in a whole Hive.

The same scenario as phase 9's first exit criterion (`tests/e2e/test_phase9_exit_criteria.py`), cut
down to a Drone: a Worker writes a file, asks for a line counter with `request_tool`, the Queen's
flow scaffolds it on the SCAFFOLDER slot, proves it in the Quarantine Comb's subprocess sandbox,
promotes it, and the Worker calls it by the name and the argument its own schema gives and closes
with the count. `run_scaffold_scenario` builds the Hive (every slot on the fake provider) and runs
it; `with_local_scaffolder` rebinds the SCAFFOLDER slot to a local OpenAI-compatible server from
`docs/manifests/local.toml`, so only the tool's author changes between the CI run and the
`local_llm` one. `grade` reduces a run to what an operator reads: promoted or not, the failing
gate, and what the Worker finished with.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped. Used by
    tests/evals/scaffold/test_scaffold_local.py; reuses the scripts of tests/e2e/royal_jelly_runs.

Key invariants:
    - Only the SCAFFOLDER binding differs between the fake and the local run; the planner, the
      Worker and the judge answer from the same script.
    - On the local run the script holds no scaffold reply, so a SCAFFOLDER call the fake answered
      would fail loudly instead of passing for the wrong reason.
    - No model id or URL is written here (`scripts/check_no_model_ids.py`): the local manifest and
      the environment name them.

See Also:
    - tests/evals/README.md for how to run the local variant.
    - docs/manifests/local.toml for the server and the model the scaffolder is bound to.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest
from e2e.kernel_helpers import single_task_plan
from e2e.royal_jelly_runs import (
    ANSWER_FILE,
    LabRun,
    LabScript,
    build_lab_hive,
    count_lines_reply,
    lab_manifest,
    pin_host_platform,
    request_then_use_turn,
    run_lab,
)

from hivemind.manifest import HiveManifest, load_manifest
from hivemind.manifest.schema.llm import SlotBinding

REPO_ROOT = Path(__file__).resolve().parents[5]
LOCAL_MANIFEST = REPO_ROOT / "docs" / "manifests" / "local.toml"
LOCAL_PROVIDER = "local"  # The one provider docs/manifests/local.toml declares.
GOAL = "Report how many lines notes.txt has."

__all__ = ["GOAL", "LOCAL_MANIFEST", "ScaffoldGrade", "grade", "run_scaffold_scenario"]


@dataclass(frozen=True, slots=True)
class ScaffoldGrade:
    """What a scaffold run is judged on."""

    promoted: bool  # `tool.promoted` is on the trail.
    failing_gate: str  # The last failing gate the Comb named; empty when none failed.
    summary: str  # What the Worker's task finished with.
    succeeded: bool  # The Worker's task reached SUCCEEDED (its acceptance held).


def with_local_scaffolder(manifest: HiveManifest, environ: Mapping[str, str]) -> HiveManifest:
    """Rebind SCAFFOLDER to the local server: its provider, its Forage source and its model.

    Args:
        manifest: A fake-provider Hive Manifest.
        environ: `HIVEMIND_LOCAL_LLM_BASE_URL` replaces the manifest's server address, and
            `HIVEMIND_LOCAL_SCAFFOLDER_MODEL` (optional) the model local.toml names.

    Returns:
        A copy with the `local` provider added, the `scaffolder` slot bound to it and its model
        priced in the Forage map; every other slot stays on the fake provider.
    """
    local = load_manifest(LOCAL_MANIFEST)
    provider = local.llm.providers[LOCAL_PROVIDER].model_copy(
        update={"base_url": environ["HIVEMIND_LOCAL_LLM_BASE_URL"]}
    )
    model = environ.get("HIVEMIND_LOCAL_SCAFFOLDER_MODEL") or local.llm.slots["scaffolder"].model
    llm = manifest.llm
    providers = {**llm.providers, LOCAL_PROVIDER: provider}
    slots = {**llm.slots, "scaffolder": SlotBinding(provider=LOCAL_PROVIDER, model=model)}
    # local.toml's one Forage source prices the local server's model; the id is that file's own.
    source_id, entry = next(iter(local.forage.map.items()))
    source = entry.model_copy(update={"model": model})
    forage = manifest.forage.model_copy(update={"map": {**manifest.forage.map, source_id: source}})
    update = {
        "llm": llm.model_copy(update={"providers": providers, "slots": slots}),
        "forage": forage,
    }
    return manifest.model_copy(update=update)


def run_scaffold_scenario(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, environ: Mapping[str, str] | None = None
) -> LabRun:
    """Run the scenario; with `environ` the SCAFFOLDER is the local model, else the fake.

    Args:
        tmp_path: A writable directory for the Hive.
        monkeypatch: Pins the host platform to Ubuntu so the sandbox path never depends on it.
        environ: The environment naming the local server; None scripts the fake scaffolder.

    Returns:
        The run, for `grade`.
    """
    pin_host_platform(monkeypatch)
    scaffolds = [count_lines_reply()] if environ is None else []
    script = LabScript(request_then_use_turn, single_task_plan(ANSWER_FILE), scaffolds)
    path = lab_manifest(tmp_path)
    manifest = load_manifest(path, {})
    if environ is not None:
        manifest = with_local_scaffolder(manifest, environ)
    hive = build_lab_hive(manifest, script)
    return asyncio.run(run_lab(hive, [GOAL]))


def grade(run: LabRun) -> ScaffoldGrade:
    """Reduce a run to its grade: promoted, the last failing gate, the Worker's closing line."""
    (report,) = run.reports
    outcome = report.tasks[0].outcome
    failed = [e.payload for e in run.of_kind("tool.quarantined") if e.payload["passed"] is False]
    return ScaffoldGrade(
        promoted="tool.promoted" in run.tool_kinds,
        failing_gate=str(failed[-1]["failing_gate"]) if failed else "",
        summary=outcome.summary if outcome is not None else "",
        succeeded=report.succeeded,
    )
