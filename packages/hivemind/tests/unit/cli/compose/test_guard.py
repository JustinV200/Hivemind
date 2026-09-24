"""Tests for hivemind.cli.compose.guard: the Guard policy names the Hive's own state (ADR-0033).

Fits into the Hive:
    Mirrors src/hivemind/cli/compose/guard.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.compose.guard for the module under test.
    - hivemind.guard.policy.floors.hive_state for the floor that reads what this names.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path

import psutil
import pytest
from builders.cli import fake_manifest

from hivemind.cli.compose import guard as guard_module
from hivemind.cli.compose.guard import build_enforcer, guard_policy, hive_state
from hivemind.guard import load_guard_policy
from hivemind.guard.policy import comparable_path
from hivemind.manifest import load_manifest
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from waggle.clock import FakeClock


def test_the_hive_state_names_the_db_its_siblings_the_secrets_and_the_manifest(
    tmp_path: Path,
) -> None:
    manifest = load_manifest(fake_manifest(tmp_path))
    db = manifest.resolve_path(manifest.hive.db)

    state = hive_state(manifest)

    assert {comparable_path(db), comparable_path(f"{db}-wal"), comparable_path(f"{db}-shm")} <= (
        state.files
    )
    assert comparable_path(tmp_path / "hive.toml") in state.files
    assert state.directories == {comparable_path(manifest.resolve_path(manifest.hive.secrets_dir))}


def test_the_hive_stands_interface_addresses_are_its_own(tmp_path: Path) -> None:
    state = hive_state(load_manifest(fake_manifest(tmp_path)))

    # Every machine answers on its loopback interface, whatever else it has.
    assert ipaddress.ip_address("127.0.0.1") in state.own_addresses


def test_an_unlistable_machine_names_no_own_address(monkeypatch: pytest.MonkeyPatch) -> None:
    def unlistable() -> object:
        raise OSError("no interfaces here")

    monkeypatch.setattr(psutil, "net_if_addrs", unlistable)

    assert guard_module.interface_addresses() == ()


def test_the_enforcers_policy_is_the_shipped_one_with_the_hive_state_on_it(
    tmp_path: Path,
) -> None:
    manifest = load_manifest(fake_manifest(tmp_path))
    clock = FakeClock()

    enforcer = build_enforcer(manifest, MemoryPheromoneTrail(clock), clock)

    assert enforcer.policy == guard_policy(manifest)
    assert enforcer.policy.roles == load_guard_policy().roles
    assert enforcer.policy.hive_state.files
