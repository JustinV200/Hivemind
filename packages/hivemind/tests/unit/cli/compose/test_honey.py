"""Tests for hivemind.cli.compose.honey: building a Hive's Honey Store handles from its manifest.

Fits into the Hive:
    Mirrors src/hivemind/cli/compose/honey.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.compose.honey for the module under test.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from builders.cli import fake_manifest

from hivemind.cell import CombShieldLevel
from hivemind.cli.compose.deps import build_fanner, build_provider_registry
from hivemind.cli.compose.honey import (
    HONEY_ACTOR,
    build_honey_access,
    resolve_embedder,
    resolve_ripener,
)
from hivemind.cli.stores import build_forage_map, open_honey_store, open_trail
from hivemind.forage import ModelSlot
from hivemind.honey_store import NectarOrigin, NectarSubmission
from hivemind.llm import FakeEmbedding, ProviderRegistry
from hivemind.manifest import HiveManifest, load_manifest
from hivemind.manifest.schema.llm import ProviderSpec, SlotBinding
from waggle.clock import FakeClock
from waggle.ids import new_cell_id
from waggle.messages.honey import NectarKind


def _registry(manifest: HiveManifest, clock: FakeClock) -> ProviderRegistry:
    forage_map = build_forage_map(manifest, clock)
    return build_provider_registry(manifest, {}, clock, forage_map, None)


def _with_embedder_on(manifest: HiveManifest, kind: str) -> HiveManifest:
    """Rebind the EMBEDDER slot to a new provider of `kind`, leaving every other slot fake."""
    providers = dict(manifest.llm.providers)
    providers["embeds"] = ProviderSpec(kind=kind, base_url="")
    slots = dict(manifest.llm.slots)
    slots["embedder"] = SlotBinding(provider="embeds", model="test-embed")
    llm = manifest.llm.model_copy(update={"providers": providers, "slots": slots})
    return manifest.model_copy(update={"llm": llm})


def test_resolve_embedder_binds_a_kind_that_can_embed(tmp_path: Path) -> None:
    clock = FakeClock()
    manifest = load_manifest(fake_manifest(tmp_path, clock=clock))

    bound = resolve_embedder(_registry(manifest, clock))

    assert bound is not None
    assert isinstance(bound.provider, FakeEmbedding)


def test_resolve_embedder_degrades_to_none_for_a_kind_with_no_embeddings(tmp_path: Path) -> None:
    # ADR-0032: an Anthropic-bound embedder is full-text search only, never a failed Hive.
    clock = FakeClock()
    manifest = _with_embedder_on(load_manifest(fake_manifest(tmp_path, clock=clock)), "anthropic")

    assert resolve_embedder(_registry(manifest, clock)) is None


def test_resolve_ripener_binds_the_ripener_slot(tmp_path: Path) -> None:
    clock = FakeClock()
    manifest = load_manifest(fake_manifest(tmp_path, clock=clock))

    bound = resolve_ripener(_registry(manifest, clock))

    assert bound is not None
    assert bound.slot is ModelSlot.RIPENER


def test_build_honey_access_wires_one_store_under_the_hives_own_identity(tmp_path: Path) -> None:
    clock = FakeClock()
    manifest = load_manifest(fake_manifest(tmp_path, clock=clock))
    db = manifest.resolve_path(manifest.hive.db)
    forage_map = build_forage_map(manifest, clock)
    registry = build_provider_registry(manifest, {}, clock, forage_map, None)
    fanner = build_fanner(manifest, forage_map, open_trail(db), clock)

    access = build_honey_access(manifest, open_honey_store(db), registry, fanner, clock)

    assert access.identity.hive_id == manifest.hive.id
    assert access.identity.actor == HONEY_ACTOR
    assert access.retrieval == manifest.honey.retrieval
    submission = NectarSubmission(
        kind=NectarKind.FINDING,
        origin=NectarOrigin.TASK_OUTCOME,
        media_type="text/plain",
        title="A verified outcome",
        content=b"The widget config lives at /etc/widgets.toml.",
        task_id=None,
        cell_id=new_cell_id(clock),
        observed_at=clock.now(),
        declared=None,
        from_borrowed_cell=False,
        tier=CombShieldLevel.MEADOW,
    )
    result = asyncio.run(access.intake.submit(submission))
    outcome = asyncio.run(access.ripener.run_pass())
    assert result.is_new
    assert outcome.ripen.ripened == 1  # Intake and the Ripener share one store.
