"""Tests for hivemind.manifest.loader: load_manifest, the shipped examples, and error messages.

Fits into the Hive:
    Mirrors src/hivemind/manifest/loader.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.manifest.loader for the module under test.
    - docs/manifests/ for the example manifests test_load_manifest_reads_every_example loads.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hivemind.manifest import HiveManifest, ManifestError, load_manifest
from hivemind.supervision import load_policy
from hivemind.supervision.capping import load_tiers

# The repository root, five parents up from this test file
# (packages/hivemind/tests/unit/manifest/test_loader.py), matching test_policy.py's own pattern.
_REPO_ROOT = Path(__file__).resolve().parents[5]
_MANIFESTS_DIR = _REPO_ROOT / "docs" / "manifests"


@pytest.mark.parametrize("filename", ["minimal.toml", "local.toml", "full.toml"])
def test_load_manifest_reads_every_example(filename: str) -> None:
    manifest = load_manifest(_MANIFESTS_DIR / filename)

    assert manifest.source_path == _MANIFESTS_DIR / filename
    assert manifest.hive.id.startswith("hive_")


@pytest.mark.parametrize("filename", ["minimal.toml", "local.toml", "full.toml"])
def test_every_example_manifests_supervision_data_actually_loads(filename: str) -> None:
    # Loading an example only proves it matches the schema. `policy_file` and `capping_tiers_file`
    # are the two manifest paths that must reach a real table before a Hive can start (`db` and
    # `scratch_root` are both created on demand), and an unset field means the table shipped in
    # hivemind.supervision.defaults -- so assert both are reachable whichever way the example goes.
    # Without this, an example can name data it cannot reach and still pass every other test, which
    # is how all three came to point at docs/manifests/docs/supervision/.
    manifest = load_manifest(_MANIFESTS_DIR / filename)

    policy = load_policy(_resolved(manifest, manifest.supervision.policy_file))
    tiers = load_tiers(_resolved(manifest, manifest.supervision.capping_tiers_file))

    assert policy.rules
    assert tiers.tiers


def _resolved(manifest: HiveManifest, path: Path | None) -> Path | None:
    """Resolve one `[supervision]` path against the manifest, passing None through unchanged."""
    return manifest.resolve_path(path) if path is not None else None


def test_load_manifest_local_toml_is_offline() -> None:
    manifest = load_manifest(_MANIFESTS_DIR / "local.toml")

    assert manifest.llm.offline is True


def test_full_toml_round_trips_through_model_dump_and_re_validation() -> None:
    manifest = load_manifest(_MANIFESTS_DIR / "full.toml")

    dumped = manifest.model_dump(mode="json")
    reloaded = HiveManifest.model_validate(dumped)

    assert reloaded.model_dump(mode="json") == dumped


def test_load_manifest_raises_for_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="Could not read"):
        load_manifest(tmp_path / "does-not-exist.toml")


def test_load_manifest_raises_for_invalid_toml(tmp_path: Path) -> None:
    bad_file = tmp_path / "bad.toml"
    bad_file.write_text("this is not [ valid toml", encoding="utf-8")

    with pytest.raises(ManifestError, match="not valid TOML"):
        load_manifest(bad_file)


def test_load_manifest_raises_with_a_dotted_location_for_a_missing_required_field(
    tmp_path: Path,
) -> None:
    # Start from a manifest that is otherwise fully valid (every slot bound, drone footprint
    # present) and remove only node_id, so the one reported error is unambiguously about it: a
    # pydantic default_factory failure elsewhere in the document can otherwise crowd out a sibling
    # "field required" error from the same model_validate call.
    original = (_MANIFESTS_DIR / "minimal.toml").read_text(encoding="utf-8")
    without_node_id = "\n".join(
        line for line in original.splitlines() if not line.startswith("node_id")
    )
    bad_file = tmp_path / "missing-node-id.toml"
    bad_file.write_text(without_node_id, encoding="utf-8")

    with pytest.raises(ManifestError, match=r"hive\.node_id") as excinfo:
        load_manifest(bad_file)
    assert str(bad_file) in str(excinfo.value)


def test_load_manifest_applies_environment_overrides() -> None:
    manifest = load_manifest(
        _MANIFESTS_DIR / "local.toml", environ={"HIVEMIND_HIVE_STAND_SCRATCH_ROOT": "/scratch"}
    )

    assert str(manifest.hive_stand.scratch_root) in ("/scratch", "\\scratch")


def test_load_manifest_rejects_an_override_that_makes_the_manifest_inconsistent() -> None:
    # minimal.toml's only provider (Anthropic) is hosted; forcing offline=true through the
    # environment must fail the same way it would if the manifest file itself set it.
    with pytest.raises(ManifestError, match="not provably local"):
        load_manifest(_MANIFESTS_DIR / "minimal.toml", environ={"HIVEMIND_LLM_OFFLINE": "true"})


def test_load_manifest_with_no_environ_argument_applies_no_overrides() -> None:
    manifest = load_manifest(_MANIFESTS_DIR / "minimal.toml")

    assert manifest.llm.offline is False
