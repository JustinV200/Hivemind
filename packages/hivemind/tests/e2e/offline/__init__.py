"""Prove offline mode end to end: whole-Hive runs under a no-egress guard, on a scripted server.

Roadmap step 8.6 and phase 8's exit criteria: the haiku goal, ripening, a spoken goal, a Clustering
round trip and the browser Forager scenario complete under `docs/manifests/offline.toml`, with every
`llm.call` on a provider that runs on this machine and not one socket connect to a non-loopback
address. This package is that suite's rig, split by responsibility (codingrules 5.2): `manifest`
derives the Hive's manifest from the shipped file, `guard` is the socket guard, `reads` judges the
trail, and `rig` runs a goal in process or through `hive run`. The cases are the `test_offline_*`
modules beside them; the `egress` fixture is in this directory's `conftest.py`.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the `test_offline_*`
    modules of this directory.

Key invariants:
    - This file holds re-exports and `__all__` only.

See Also:
    - docs/manifests/offline.toml for the file every case derives its manifest from.
    - docs/adr/0045-provider-routing-forage-aware-fallbacks-and-offline-mode.md for the proof.

Public API:
    - OFFLINE_MANIFEST, offline_manifest, scripted_model_ids, dump_toml: the derived manifest.
    - EgressLog, install_no_egress: the socket guard.
    - assert_every_call_local, calls_on, llm_calls, trail_of: reading the trail.
    - GOAL, HAIKU_FILES, Outcome, run_goal_by_cli, run_goal_in_process: running a goal.
"""

from e2e.offline.guard import EgressLog, install_no_egress
from e2e.offline.manifest import (
    OFFLINE_MANIFEST,
    dump_toml,
    offline_manifest,
    scripted_model_ids,
)
from e2e.offline.reads import assert_every_call_local, calls_on, llm_calls, trail_of
from e2e.offline.rig import (
    GOAL,
    HAIKU_FILES,
    Outcome,
    run_goal_by_cli,
    run_goal_in_process,
)

__all__ = [
    "GOAL",
    "HAIKU_FILES",
    "OFFLINE_MANIFEST",
    "EgressLog",
    "Outcome",
    "assert_every_call_local",
    "calls_on",
    "dump_toml",
    "install_no_egress",
    "llm_calls",
    "offline_manifest",
    "run_goal_by_cli",
    "run_goal_in_process",
    "scripted_model_ids",
    "trail_of",
]
