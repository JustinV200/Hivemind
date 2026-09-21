"""Unit tests for hivemind.hive.backends.cloud.fake: FakeCloudCellBackend's own extra surface.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors
    src/hivemind/hive/backends/cloud/fake.py (codingrules section 3). The cross-implementation
    contract (idempotent destroy, capability honouring, ...) lives in
    packages/hivemind/tests/contracts/test_cell_backend_contract.py instead; this module covers
    what is specific to a cloud backend: cost accrual over the injected Clock, and UnknownCellError
    for a Cell this instance never provisioned.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.backends.cloud.fake for FakeCloudCellBackend, under test.
"""

from __future__ import annotations

import pytest
from builders.forage import make_capacity
from pydantic import SecretStr

from hivemind.hive.backends.cloud.base import (
    CloudBackendConfig,
    CloudCredentials,
    CloudRegion,
    PricingTag,
)
from hivemind.hive.backends.cloud.fake import FakeCloudCellBackend
from hivemind.hive.errors import UnknownCellError
from hivemind.hive.models import VirtualCellSpec
from waggle.clock import FakeClock
from waggle.ids import CellId, new_hive_id


def _make_spec(**overrides: object) -> VirtualCellSpec:
    """Build a valid VirtualCellSpec, with sensible defaults for every field a test ignores."""
    fields: dict[str, object] = {
        "image": "base-ubuntu",
        "cpu_cores": 2.0,
        "memory_bytes": 2 * 1024**3,
        "disk_bytes": 10 * 1024**3,
        "capacity": make_capacity(),
        "hive_id": new_hive_id(FakeClock()),
    }
    fields.update(overrides)
    return VirtualCellSpec(**fields)


def _make_backend(clock: FakeClock, *, cost_per_hour_usd: float = 1.0) -> FakeCloudCellBackend:
    """Build a fresh FakeCloudCellBackend at `cost_per_hour_usd`."""
    config = CloudBackendConfig(
        region=CloudRegion("us-east-1"),
        credentials=CloudCredentials(key_id=SecretStr("k"), secret=SecretStr("s")),
        pricing=PricingTag(cost_per_hour_usd=cost_per_hour_usd),
        instance_type="t3.medium",
    )
    return FakeCloudCellBackend(clock, config)


async def test_accrued_cost_usd_grows_with_elapsed_clock_time() -> None:
    clock = FakeClock()
    backend = _make_backend(clock, cost_per_hour_usd=2.0)
    cell = await backend.provision(_make_spec())

    clock.advance(3600)  # One simulated hour.

    assert await backend.accrued_cost_usd(cell.id) == pytest.approx(2.0)


async def test_accrued_cost_usd_freezes_once_the_cell_is_destroyed() -> None:
    clock = FakeClock()
    backend = _make_backend(clock, cost_per_hour_usd=1.0)
    cell = await backend.provision(_make_spec())
    clock.advance(3600)
    await backend.destroy(cell.id)

    clock.advance(3600)  # Elapsed time after teardown must not keep accruing.

    assert await backend.accrued_cost_usd(cell.id) == pytest.approx(1.0)


async def test_accrued_cost_usd_raises_for_a_cell_never_provisioned() -> None:
    backend = _make_backend(FakeClock())

    with pytest.raises(UnknownCellError):
        await backend.accrued_cost_usd(CellId("cell_never_provisioned"))


async def test_config_property_returns_the_backends_own_config() -> None:
    clock = FakeClock()
    config = CloudBackendConfig(
        region=CloudRegion("eu-west-1"),
        credentials=CloudCredentials(key_id=SecretStr("k"), secret=SecretStr("s")),
        pricing=PricingTag(cost_per_hour_usd=0.5),
        instance_type="e2-standard-4",
    )
    backend = FakeCloudCellBackend(clock, config)

    assert backend.config is config


async def test_set_provision_failure_delegates_to_the_internal_fake_cell_backend() -> None:
    backend = _make_backend(FakeClock())
    backend.set_provision_failure("out of capacity")

    with pytest.raises(Exception, match="out of capacity"):
        await backend.provision(_make_spec())
