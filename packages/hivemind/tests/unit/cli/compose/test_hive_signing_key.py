"""Tests for hivemind.cli.compose.hive's Hive signing key: persisted, not minted per process.

Phase 5 open item 5: the Queen signs every Virtual Cell frame, so `build_hive` must hand
`build_virtual_cells` the Hive's persisted key from the secret store at `[hive] secrets_dir`,
and the same key on every build. The e2e Virtual Cell scenarios drive a real in-Cell Warden that
verifies those frames; these tests pin the composition itself.

Fits into the Hive:
    Mirrors src/hivemind/cli/compose/hive/build.py (codingrules section 3), split out of
    tests/unit/cli/test_compose.py by feature (codingrules 5.1).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.compose.hive for `_virtual_side`, the function under test.
    - hivemind.common.secrets.signers for load_or_mint_hive_signer.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from builders.cli import fake_manifest

from hivemind.cli.compose import build_hive
from hivemind.cli.compose.hive import build as hive_module
from hivemind.cli.compose.virtual_cells import VirtualCellsParts
from hivemind.common.secrets import HIVE_SIGNING_KEY, FileSecretStore
from hivemind.manifest import HiveManifest, load_manifest
from hivemind.manifest.schema.placement import VirtualCellsSection
from hivemind.pheromone import PheromoneTrail
from waggle.clock import Clock, FakeClock
from waggle.signing import Ed25519Signer


class _SignerSpy:
    """Stand in for build_virtual_cells: record the hive_signer build_hive hands it."""

    def __init__(self) -> None:
        self.signers: list[Ed25519Signer | None] = []

    def __call__(
        self,
        manifest: HiveManifest,
        trail: PheromoneTrail,
        clock: Clock,
        environ: object = None,
        *,
        hive_signer: Ed25519Signer | None = None,
    ) -> VirtualCellsParts | None:
        """Record the signer and build no Virtual side, so nothing else needs a backend."""
        self.signers.append(hive_signer)
        return None


def _manifest(tmp_path: Path, *, virtual: bool) -> HiveManifest:
    """Load a test manifest from tmp_path, with a `fake` Virtual side when ``virtual``."""
    manifest = load_manifest(fake_manifest(tmp_path), {})
    if not virtual:
        return manifest
    return manifest.model_copy(update={"virtual_cells": VirtualCellsSection(backend="fake")})


def _stored_key(tmp_path: Path) -> bytes | None:
    """Read the Hive's key the way the next process would: from the resolved secrets_dir."""
    return asyncio.run(FileSecretStore(tmp_path / "secrets").get(HIVE_SIGNING_KEY))


def test_build_hive_hands_the_same_persisted_key_to_every_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = _SignerSpy()
    monkeypatch.setattr(hive_module, "build_virtual_cells", spy)
    manifest = _manifest(tmp_path, virtual=True)

    build_hive(manifest, environ={}, clock=FakeClock())
    build_hive(manifest, environ={}, clock=FakeClock())

    first, second = spy.signers
    assert first is not None and second is not None
    assert first.public_key_bytes == second.public_key_bytes


def test_the_key_lands_in_the_manifests_secrets_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = _SignerSpy()
    monkeypatch.setattr(hive_module, "build_virtual_cells", spy)

    build_hive(_manifest(tmp_path, virtual=True), environ={}, clock=FakeClock())

    signer = spy.signers[0]
    assert signer is not None
    assert _stored_key(tmp_path) == signer.private_key_bytes


def test_a_hive_with_no_virtual_side_writes_no_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = _SignerSpy()
    monkeypatch.setattr(hive_module, "build_virtual_cells", spy)

    build_hive(_manifest(tmp_path, virtual=False), environ={}, clock=FakeClock())

    assert spy.signers == [None]
    assert not (tmp_path / "secrets").exists()
