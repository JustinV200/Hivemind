"""Tests for the `[royal_jelly]` and `[basket]` sections and the sandbox cross-section check.

Fits into the Hive:
    Mirrors src/hivemind/manifest/schema/royal_jelly.py (codingrules section 3) and the sandbox
    validator on HiveManifest.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.manifest.schema.royal_jelly for the module under test.
    - docs/manifests/full.toml, which documents every key tested here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.cli import fake_manifest
from pydantic import ValidationError

from hivemind.manifest import ManifestError, load_manifest
from hivemind.manifest.schema import (
    BasketSection,
    HiveManifest,
    QuarantineLimitsSection,
    RoyalJellySection,
    SandboxKind,
)
from hivemind.royal_jelly import QuarantineLimits
from waggle.messages.base import MAX_CHUNK_BYTES


def _load(tmp_path: Path, extra: str = "") -> HiveManifest:
    """Write the shared fake manifest plus `extra` TOML and load it through the real loader."""
    path = fake_manifest(tmp_path)
    if extra:
        path.write_text(path.read_text(encoding="utf-8") + "\n" + extra, encoding="utf-8")
    return load_manifest(path)


def test_royal_jelly_defaults_name_a_virtual_sandbox_and_the_comb_limits() -> None:
    section = RoyalJellySection()

    assert section.sandbox is SandboxKind.VIRTUAL
    assert (section.sandbox_image, section.sandbox_distribution) == ("base-ubuntu", "ubuntu")
    assert (section.max_retries, section.request_timeout_s) == (1, 600)
    assert section.sandbox_cpu_cores > 0 and section.sandbox_lifetime_s > 0


def test_the_limits_section_mirrors_the_combs_own_limits_field_for_field() -> None:
    # The Comb's model lives a layer above the manifest, so equality is pinned here instead.
    assert QuarantineLimitsSection().model_dump() == QuarantineLimits().model_dump()


@pytest.mark.parametrize("retries", [-1, 4])
def test_max_retries_is_bounded_zero_to_three(retries: int) -> None:
    with pytest.raises(ValidationError):
        RoyalJellySection(max_retries=retries)


@pytest.mark.parametrize("retries", [0, 3])
def test_max_retries_accepts_both_ends(retries: int) -> None:
    assert RoyalJellySection(max_retries=retries).max_retries == retries


@pytest.mark.parametrize("field", ["request_timeout_s", "sandbox_cpu_cores", "sandbox_disk_bytes"])
def test_royal_jelly_numbers_must_be_positive(field: str) -> None:
    with pytest.raises(ValidationError):
        RoyalJellySection(**{field: 0})


@pytest.mark.parametrize(
    ("field", "value"),
    [("fuzz_cases", 0), ("fuzz_cases", 1_001), ("memory_bytes", 1), ("cpu_s", 0)],
)
def test_limits_reject_values_outside_their_bounds(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        QuarantineLimitsSection(**{field: value})


def test_basket_defaults() -> None:
    section = BasketSection()

    assert section.root is None
    assert section.max_blob_bytes == 256 * 1024**2
    assert section.chunk_bytes == MAX_CHUNK_BYTES == 262_144
    assert section.default_resident_ceiling_bytes == 2 * 1024**3
    assert section.ship_on_promotion is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("chunk_bytes", MAX_CHUNK_BYTES + 1),
        ("chunk_bytes", 0),
        ("max_blob_bytes", 0),
        ("max_blob_bytes", 2 * 1024**3),
        ("default_resident_ceiling_bytes", 0),
    ],
)
def test_basket_rejects_values_outside_its_bounds(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        BasketSection(**{field: value})


def test_the_manifest_carries_both_sections_by_default(tmp_path: Path) -> None:
    manifest = _load(tmp_path)

    assert isinstance(manifest.royal_jelly, RoyalJellySection)
    assert isinstance(manifest.basket, BasketSection)


def test_an_explicit_virtual_sandbox_without_a_backend_is_refused_naming_the_dev_option(
    tmp_path: Path,
) -> None:
    with pytest.raises(ManifestError, match="subprocess"):
        _load(tmp_path, '[royal_jelly]\nsandbox = "virtual"\n')


def test_an_explicit_virtual_sandbox_with_a_backend_is_accepted(tmp_path: Path) -> None:
    extra = '[virtual_cells]\nbackend = "fake"\n\n[royal_jelly]\nsandbox = "virtual"\n'

    assert _load(tmp_path, extra).royal_jelly.sandbox is SandboxKind.VIRTUAL


def test_the_subprocess_sandbox_needs_no_backend(tmp_path: Path) -> None:
    manifest = _load(tmp_path, '[royal_jelly]\nsandbox = "subprocess"\n')

    assert manifest.royal_jelly.sandbox is SandboxKind.SUBPROCESS
    assert manifest.virtual_cells.backend is None


def test_a_manifest_that_never_mentions_the_lab_still_loads_without_a_backend(
    tmp_path: Path,
) -> None:
    # Every Real-only Hive predates the Lab; the unstated default must not break it.
    manifest = _load(tmp_path)

    assert manifest.virtual_cells.backend is None
    assert manifest.royal_jelly.sandbox is SandboxKind.VIRTUAL


def test_the_sections_round_trip_through_the_toml_loader(tmp_path: Path) -> None:
    manifest = _load(
        tmp_path,
        """
[virtual_cells]
backend = "fake"

[royal_jelly]
sandbox = "virtual"
sandbox_image = "tools-ubuntu"
max_retries = 2

[royal_jelly.limits]
fuzz_cases = 8

[basket]
root = "blobs"
chunk_bytes = 65536
ship_on_promotion = false
""",
    )

    assert manifest.royal_jelly.sandbox_image == "tools-ubuntu"
    assert (manifest.royal_jelly.max_retries, manifest.royal_jelly.limits.fuzz_cases) == (2, 8)
    assert manifest.basket.chunk_bytes == 65_536 and manifest.basket.ship_on_promotion is False
    # A relative root resolves against the manifest's own directory, like every other path.
    assert manifest.basket.root is not None
    assert manifest.resolve_path(manifest.basket.root) == tmp_path / "blobs"
    dumped = manifest.model_dump(mode="json")
    assert HiveManifest.model_validate(dumped).model_dump(mode="json") == dumped


def test_an_unknown_royal_jelly_key_is_refused() -> None:
    with pytest.raises(ValidationError):
        RoyalJellySection(**{"sandbx": "virtual"})
