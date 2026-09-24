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
from typing import cast

from builders.cli import fake_manifest
from structlog.testing import capture_logs

from hivemind.cell import CombShieldLevel
from hivemind.cli.compose.deps import build_fanner, build_provider_registry
from hivemind.cli.compose.honey import (
    HONEY_ACTOR,
    build_honey_access,
    resolve_embedder,
    resolve_judge,
    resolve_ripener,
)
from hivemind.cli.stores import build_forage_map, open_honey_store, open_trail
from hivemind.forage import ModelSlot
from hivemind.honey_store import HoneyAccess, ModelClearanceJudge, NectarOrigin, NectarSubmission
from hivemind.llm import BoundModel, FakeEmbedding, ProviderRegistry
from hivemind.manifest import HiveManifest, HoneyLoweringSection, load_manifest
from hivemind.manifest.schema.llm import ProviderSpec, SlotBinding
from waggle.clock import FakeClock
from waggle.ids import new_cell_id
from waggle.messages.honey import NectarKind


def _registry(manifest: HiveManifest, clock: FakeClock) -> ProviderRegistry:
    forage_map = build_forage_map(manifest, clock)
    return build_provider_registry(manifest, {}, clock, forage_map, None)


def _access(manifest: HiveManifest, clock: FakeClock) -> HoneyAccess:
    """Build the Hive's HoneyAccess over its own file, exactly as a Hive does."""
    db = manifest.resolve_path(manifest.hive.db)
    forage_map = build_forage_map(manifest, clock)
    registry = build_provider_registry(manifest, {}, clock, forage_map, None)
    fanner = build_fanner(manifest, forage_map, open_trail(db), clock)
    return build_honey_access(manifest, open_honey_store(db), registry, fanner, clock)


def _with_judge_on(manifest: HiveManifest, provider: str) -> HiveManifest:
    """Rebind the JUDGE slot to `provider`, a name the registry may not know."""
    slots = dict(manifest.llm.slots)
    slots["judge"] = SlotBinding(provider=provider, model="test-judge")
    return manifest.model_copy(update={"llm": manifest.llm.model_copy(update={"slots": slots})})


def _with_lowering_off(manifest: HiveManifest) -> HiveManifest:
    """Switch `[honey.lowering]` off, as `enabled = false` in the manifest does."""
    honey = manifest.honey.model_copy(update={"lowering": HoneyLoweringSection(enabled=False)})
    return manifest.model_copy(update={"honey": honey})


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


def test_build_honey_access_binds_the_clearance_judge_and_lowering_settings(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    manifest = load_manifest(fake_manifest(tmp_path, clock=clock))

    access = _access(manifest, clock)

    assert isinstance(access.judge, ModelClearanceJudge)
    assert access.lowering == manifest.honey.lowering


def test_resolve_judge_binds_the_judge_slot(tmp_path: Path) -> None:
    clock = FakeClock()
    manifest = load_manifest(fake_manifest(tmp_path, clock=clock))

    bound = resolve_judge(_registry(manifest, clock), manifest.honey.lowering)

    assert bound is not None
    assert bound.slot is ModelSlot.JUDGE


def test_an_unresolvable_judge_degrades_to_none_and_says_why_once(tmp_path: Path) -> None:
    # ADR-0034: with no judge every proposal waits for the human; the Hive still builds.
    clock = FakeClock()
    manifest = _with_judge_on(load_manifest(fake_manifest(tmp_path, clock=clock)), "nowhere")

    with capture_logs() as logs:
        access = _access(manifest, clock)

    assert access.judge is None
    (logged,) = [log for log in logs if log["event"] == "honey.judge_unavailable"]
    assert "nowhere" in logged["reason"]


class _NoResolveRegistry:
    """A registry stand-in that fails the test if any slot is resolved at all."""

    def bound(self, slot: ModelSlot) -> BoundModel:
        raise AssertionError(f"{slot} was resolved although lowering is switched off")


def test_lowering_switched_off_never_resolves_the_judge_slot() -> None:
    # ADR-0034: `enabled = false` means no judge is ever called, so it is never even resolved.
    registry = cast(ProviderRegistry, _NoResolveRegistry())

    assert resolve_judge(registry, HoneyLoweringSection(enabled=False)) is None


def test_build_honey_access_with_lowering_switched_off_builds_no_judge(tmp_path: Path) -> None:
    clock = FakeClock()
    manifest = _with_lowering_off(load_manifest(fake_manifest(tmp_path, clock=clock)))

    access = _access(manifest, clock)

    assert access.judge is None
    assert access.lowering.enabled is False
