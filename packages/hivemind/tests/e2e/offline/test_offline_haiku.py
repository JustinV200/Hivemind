"""End-to-end: the haiku goal on the Hive Stand under `offline.toml`, in process and by `hive run`.

Phase 8's first exit criterion, the simplest half: the three-haiku goal completes on a Hive whose
manifest is derived from `docs/manifests/offline.toml` (`offline = true`, one `openai_compat`
provider on loopback), with every `llm.call` on a provider that runs on this machine and not one
socket connect to a non-loopback address (the guard of `tests/e2e/offline/guard.py`). The model is
a scripted OpenAI-compatible server on a free loopback port, reached by the registry's real adapter
over a real socket; the Queen plans, a Drone writes the three files through the weak-model
(prompted tool protocol, JSON mode) rungs the shipped manifest declares, and the Capping gate's
judge approves. The same goal runs twice, once through `run_goal` and once through `hive run`.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - Each case builds its own Hive, database and scripted server.

See Also:
    - tests/e2e/offline/__init__.py for the rig.
    - tests/e2e/test_kernel_on_hive_stand.py for scenario (a), which these mirror.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from e2e.offline import (
    HAIKU_FILES,
    EgressLog,
    Outcome,
    assert_every_call_local,
    calls_on,
    offline_manifest,
    run_goal_by_cli,
    run_goal_in_process,
)
from e2e.scripted_openai import Scenario, serve_in_thread

from hivemind.forage.slots import ModelSlot
from hivemind.manifest import HiveManifest

pytestmark = pytest.mark.e2e


def _assert_offline_haiku(outcome: Outcome, egress: EgressLog, manifest: HiveManifest) -> None:
    """The goal succeeded, the Drone and the Queen both called a local provider, nothing left."""
    assert outcome.succeeded
    assert_every_call_local(outcome.events, manifest)
    # The planner and the Drone both ran on the scripted server, not only a judge.
    assert calls_on(outcome.events, ModelSlot.QUEEN) and calls_on(outcome.events, ModelSlot.WORKER)
    egress.assert_clean_and_live()


def test_the_haiku_goal_completes_in_process_with_no_egress(
    tmp_path: Path, egress: EgressLog
) -> None:
    with serve_in_thread(Scenario(files=HAIKU_FILES)) as base_url:
        manifest, _path = offline_manifest(tmp_path, base_url)
        outcome = run_goal_in_process(manifest)

    _assert_offline_haiku(outcome, egress, manifest)


def test_the_haiku_goal_completes_through_hive_run_with_no_egress(
    tmp_path: Path, egress: EgressLog
) -> None:
    with serve_in_thread(Scenario(files=HAIKU_FILES)) as base_url:
        manifest, path = offline_manifest(tmp_path, base_url)
        outcome = run_goal_by_cli(manifest, path)

    assert "task.succeeded" in outcome.output
    _assert_offline_haiku(outcome, egress, manifest)
