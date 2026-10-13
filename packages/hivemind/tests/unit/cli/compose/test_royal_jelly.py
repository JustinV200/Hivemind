"""Tests for hivemind.cli.compose.royal_jelly: the Lab's and the Basket's handles for one Hive.

Fits into the Hive:
    Mirrors src/hivemind/cli/compose/royal_jelly/ (codingrules section 3). Every access here is
    built over a real `builders.cli.fake_manifest`, the Hive's own SQLite file and its real Honey
    Store, so the Honey adapter's Nectar lands in a store the test can read back.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.compose.royal_jelly for the package under test.
    - hivemind.basket for the deposit door whose Honey port the adapter fills.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

from builders.basket import make_deposit_request
from builders.cli import fake_manifest
from structlog.testing import capture_logs

from hivemind.basket import HoneyRecord, MediaKind
from hivemind.cell import HoneyClearance, hive_stand_cell_id
from hivemind.cell.local import host_platform
from hivemind.cli.compose.deps import HiveStores, build_fanner, open_default_stores
from hivemind.cli.compose.honey import build_honey_access
from hivemind.cli.compose.royal_jelly import (
    NectarHoneyPort,
    build_royal_jelly,
    resolve_scaffolder,
)
from hivemind.cli.stores import build_forage_map, build_provider_registry
from hivemind.honey_store import NectarIntake, NectarSubmission
from hivemind.llm import ProviderRegistry
from hivemind.manifest import HiveManifest, SandboxKind, load_manifest
from hivemind.manifest.schema.llm import SlotBinding
from hivemind.royal_jelly import QuarantineLimits, RoyalJellyAccess
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_task_id, new_worker_id


class _Kit:
    """One Hive's stores, registry and Honey handles, as `build_hive` would hold them."""

    def __init__(self, tmp_path: Path, manifest: HiveManifest | None = None) -> None:
        self.clock = FakeClock()
        self.manifest = manifest or load_manifest(fake_manifest(tmp_path), {})
        self.stores = open_default_stores(self.manifest)
        forage_map = build_forage_map(self.manifest, self.clock)
        self.registry = build_provider_registry(self.manifest, {}, self.clock, forage_map, None)
        assert self.stores.honey is not None
        fanner = build_fanner(self.manifest, forage_map, self.stores.trail, self.clock)
        self.honey = build_honey_access(
            self.manifest, self.stores.honey, self.registry, fanner, self.clock
        )

    def access(self, *, honey: bool = True) -> RoyalJellyAccess:
        """Build the access, with the Honey handles or without."""
        built = build_royal_jelly(
            self.manifest,
            self.stores,
            self.registry,
            self.honey if honey else None,
            self.clock,
        )
        assert built is not None
        return built


def _rebind_scaffolder(manifest: HiveManifest, provider: str) -> HiveManifest:
    """Bind the SCAFFOLDER slot to `provider`, a name the registry may not know."""
    slots = dict(manifest.llm.slots)
    slots["scaffolder"] = SlotBinding(provider=provider, model="test-scaffolder")
    return manifest.model_copy(update={"llm": manifest.llm.model_copy(update={"slots": slots})})


def test_the_access_wraps_the_hives_own_stores_and_policy(tmp_path: Path) -> None:
    kit = _Kit(tmp_path)

    access = kit.access()

    assert access.registry is kit.stores.comb_registry and access.basket is kit.stores.basket
    assert access.config is kit.manifest.royal_jelly and access.basket_config is kit.manifest.basket
    assert access.limits == QuarantineLimits()
    assert access.sandbox_kind is SandboxKind.VIRTUAL


def test_the_basket_signs_as_the_hive_node_and_a_verifier_trusts_only_that_key(
    tmp_path: Path,
) -> None:
    kit = _Kit(tmp_path)
    access = kit.access()

    outcome = asyncio.run(access.deposits.deposit(make_deposit_request(b"signed by the Hive")))
    manifest = asyncio.run(access.basket.manifest(outcome.blob.sha256))

    manifest.verify(access.verifier)  # Raises when the signature or the signer is not trusted.
    assert manifest.signer == access.signer_id == kit.manifest.hive.node_id


def test_the_hive_stand_resident_view_has_a_positive_ceiling_from_the_manifest(
    tmp_path: Path,
) -> None:
    kit = _Kit(tmp_path)

    resident = kit.access().resident

    assert resident.cell_id == hive_stand_cell_id(kit.manifest.hive.node_id)
    assert resident.ceiling_bytes == kit.manifest.basket.default_resident_ceiling_bytes > 0


def test_each_new_blob_submits_one_nectar_keyed_by_its_hash(tmp_path: Path) -> None:
    kit = _Kit(tmp_path)
    access = kit.access()

    first = asyncio.run(access.deposits.deposit(make_deposit_request(b"blob one")))
    asyncio.run(access.deposits.deposit(make_deposit_request(b"blob one")))  # A repeat.
    second = asyncio.run(access.deposits.deposit(make_deposit_request(b"blob two")))

    async def read() -> tuple[bool, bool, int]:
        store = kit.honey.store
        stats = await store.stats()
        return (
            await store.has_source(f"basket:{first.blob.sha256}"),
            await store.has_source(f"basket:{second.blob.sha256}"),
            sum(stats.nectar_by_state.values()),
        )

    found_first, found_second, nectar_rows = asyncio.run(read())
    assert (found_first, found_second) == (True, True)
    assert nectar_rows == 2  # The repeat deposit wrote no second record.


class _CapturingIntake:
    """Stand in for NectarIntake: remember every submission."""

    def __init__(self) -> None:
        self.submissions: list[NectarSubmission] = []

    async def submit(self, submission: NectarSubmission) -> None:
        self.submissions.append(submission)


def _record(origin_cell: str, **overrides: object) -> HoneyRecord:
    clock = FakeClock()
    fields: dict[str, object] = {
        "title": "A wheel set",
        "summary": "Offline wheels for the numeric stack.",
        "sha256": "c" * 64,
        "media_kind": MediaKind.WHEEL,
        "clearance": HoneyClearance.C1,
        "origin_cell_id": origin_cell,
        "origin_task_id": new_task_id(clock),
        "origin_bee": new_worker_id(clock),
        "deposited_at": clock.now(),
    }
    fields.update(overrides)
    return HoneyRecord(**fields)


def test_the_adapter_builds_the_nectar_from_the_record_alone() -> None:
    stand = new_cell_id(FakeClock())
    intake = _CapturingIntake()
    record = _record(stand)

    asyncio.run(NectarHoneyPort(cast(NectarIntake, intake), stand).record(record))

    (submission,) = intake.submissions
    assert submission.source_key == f"basket:{record.sha256}"
    assert (submission.cell_id, submission.task_id, submission.bee) == (
        record.origin_cell_id,
        record.origin_task_id,
        record.origin_bee,
    )
    assert submission.declared is HoneyClearance.C1
    text = submission.content.decode("utf-8")
    assert record.sha256 in text and record.summary in text and "WHEEL" in text


def test_the_adapter_treats_only_the_hive_stand_as_a_borrowed_cell() -> None:
    stand = new_cell_id(FakeClock())
    intake = _CapturingIntake()
    port = NectarHoneyPort(cast(NectarIntake, intake), stand)

    asyncio.run(port.record(_record(stand)))
    asyncio.run(port.record(_record(new_cell_id(FakeClock()), sha256="d" * 64)))

    from_stand, from_elsewhere = intake.submissions
    assert from_stand.from_borrowed_cell is True
    assert from_elsewhere.from_borrowed_cell is False


def test_without_a_honey_store_the_record_is_dropped_with_a_warning(tmp_path: Path) -> None:
    access = _Kit(tmp_path).access(honey=False)

    with capture_logs() as logs:
        outcome = asyncio.run(access.deposits.deposit(make_deposit_request(b"no honey here")))

    assert outcome.is_new  # The blob is stored regardless of the missing record.
    assert any(entry["event"] == "royal_jelly.honey_unavailable" for entry in logs)


def test_the_subprocess_sandbox_logs_a_dev_only_warning(tmp_path: Path) -> None:
    base = load_manifest(fake_manifest(tmp_path), {})
    config = base.royal_jelly.model_copy(update={"sandbox": SandboxKind.SUBPROCESS})
    kit = _Kit(tmp_path, base.model_copy(update={"royal_jelly": config}))

    with capture_logs() as logs:
        access = kit.access()

    assert access.sandbox_kind is SandboxKind.SUBPROCESS
    warnings = [e for e in logs if e["event"] == "royal_jelly.subprocess_sandbox"]
    assert len(warnings) == 1 and "dev only" in warnings[0]["note"]


def test_the_subprocess_sandbox_is_this_host_so_the_path_compares_with_it(tmp_path: Path) -> None:
    base = load_manifest(fake_manifest(tmp_path), {})
    config = base.royal_jelly.model_copy(update={"sandbox": SandboxKind.SUBPROCESS})
    family, distribution = host_platform()

    access = _Kit(tmp_path, base.model_copy(update={"royal_jelly": config})).access()

    # The dev sandbox runs on the Hive Stand, so a tool for this very platform takes the sandbox
    # route whatever distribution the manifest's default image names (an Arch host is not Ubuntu).
    assert access.sandbox_os is family
    assert access.config.sandbox_distribution == (distribution or "ubuntu")


def test_an_explicit_sandbox_distribution_is_kept_for_the_subprocess_sandbox(
    tmp_path: Path,
) -> None:
    base = load_manifest(fake_manifest(tmp_path), {})
    config = base.royal_jelly.model_copy(
        update={"sandbox": SandboxKind.SUBPROCESS, "sandbox_distribution": "debian"}
    )

    access = _Kit(tmp_path, base.model_copy(update={"royal_jelly": config})).access()

    assert access.config.sandbox_distribution == "debian"


def test_a_virtual_sandbox_logs_no_dev_warning(tmp_path: Path) -> None:
    with capture_logs() as logs:
        _Kit(tmp_path).access()

    assert not any(e["event"] == "royal_jelly.subprocess_sandbox" for e in logs)


def test_the_scaffolder_is_bound_when_the_slot_resolves(tmp_path: Path) -> None:
    assert _Kit(tmp_path).access().scaffolder_bound is not None


def test_an_unbound_scaffolder_slot_degrades_to_none_and_logs_once(tmp_path: Path) -> None:
    manifest = _rebind_scaffolder(load_manifest(fake_manifest(tmp_path), {}), "not_a_provider")
    kit = _Kit(tmp_path, manifest)

    with capture_logs() as logs:
        access = kit.access()

    assert access.scaffolder_bound is None
    unavailable = [e for e in logs if e["event"] == "royal_jelly.scaffolder_unavailable"]
    assert len(unavailable) == 1 and unavailable[0]["reason"]
    # The registry and the Basket work without a scaffolder.
    assert asyncio.run(access.registry.list_tools()) == ()


def test_resolve_scaffolder_returns_none_for_an_unknown_provider(tmp_path: Path) -> None:
    manifest = _rebind_scaffolder(load_manifest(fake_manifest(tmp_path), {}), "nobody")
    clock = FakeClock()
    registry: ProviderRegistry = build_provider_registry(
        manifest, {}, clock, build_forage_map(manifest, clock), None
    )

    assert resolve_scaffolder(registry) is None


def test_hand_built_stores_without_the_registry_or_basket_give_no_access(tmp_path: Path) -> None:
    kit = _Kit(tmp_path)
    bare = HiveStores(
        trail=kit.stores.trail,
        chamber=kit.stores.chamber,
        memory=kit.stores.memory,
        leavings=kit.stores.leavings,
        goal_requests=kit.stores.goal_requests,
        chat=kit.stores.chat,
    )

    assert build_royal_jelly(kit.manifest, bare, kit.registry, None, kit.clock) is None
