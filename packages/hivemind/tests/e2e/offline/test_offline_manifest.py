"""The derived manifest keeps what `offline.toml` promises and changes only what the proof forces.

Every case in this directory runs under a manifest derived from `docs/manifests/offline.toml`, so
that derivation is itself under test: the Hive stays offline (`offline = true`, which makes the
schema refuse any provider off loopback), keeps the shipped file's Hive and node ids, its slot to
provider wiring and its `[forage.map]` grades, and every provider ends up an `openai_compat` row on
the scripted server's loopback address (the in-process embedder and Whisper of the shipped file need
weights this container cannot fetch). If someone edits `offline.toml`, the cases follow.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - The derivation is loaded through `load_manifest`, so the schema's own offline rule has run.

See Also:
    - tests/e2e/offline/manifest.py for the derivation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from e2e.offline import OFFLINE_MANIFEST, offline_manifest, scripted_model_ids

from hivemind.cli.stores import provider_configs
from hivemind.llm.registry import runs_locally
from hivemind.manifest import load_manifest

pytestmark = pytest.mark.e2e

_URL = "http://127.0.0.1:9/v1"  # Never dialled: the manifest is only loaded.


def test_the_derived_manifest_stays_offline_and_changes_only_what_the_scripted_server_forces(
    tmp_path: Path,
) -> None:
    shipped = load_manifest(OFFLINE_MANIFEST, {})

    derived, written = offline_manifest(tmp_path, _URL)

    assert derived.llm.offline and shipped.llm.offline
    assert (derived.hive.id, derived.hive.node_id) == (shipped.hive.id, shipped.hive.node_id)
    # Each slot is still bound to the provider the shipped file names; only the model id moved.
    assert {k: v.provider for k, v in derived.llm.slots.items()} == {
        k: v.provider for k, v in shipped.llm.slots.items()
    }
    assert {k: v.model for k, v in derived.llm.slots.items()} == scripted_model_ids()
    # The Forage map keeps its rows, providers and hand-set grades.
    assert {k: (v.provider, v.grade) for k, v in derived.forage.map.items()} == {
        k: (v.provider, v.grade) for k, v in shipped.forage.map.items()
    }
    assert all(runs_locally(config) for config in provider_configs(derived).values())
    assert {spec.kind for spec in derived.llm.providers.values()} == {"openai_compat"}
    assert written == tmp_path / "hive.toml" and written.is_file()
