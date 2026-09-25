"""Tests for hivemind.guard.policy.hive_state: the Hive's own state, in the spelling floors compare.

Fits into the Hive:
    Mirrors src/hivemind/guard/policy/hive_state.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.guard.policy.hive_state for the module under test.
"""

from __future__ import annotations

import ipaddress
from pathlib import PurePosixPath, PureWindowsPath

import pytest

from hivemind.guard.policy import GuardPolicy, HiveState, comparable_path, load_guard_policy


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/srv/Hive/hive.db", "/srv/hive/hive.db"),
        ("/srv/hive/scratch/../hive.db", "/srv/hive/hive.db"),
        ("/srv/./hive//hive.db", "/srv/hive/hive.db"),
        ("//srv/hive", "/srv/hive"),
        (r"C:\Hive\HIVE.DB", "c:/hive/hive.db"),
        ("/srv/hive/", "/srv/hive"),
    ],
)
def test_comparable_path_normalises_separators_dots_and_case(raw: str, expected: str) -> None:
    assert comparable_path(raw) == expected


def test_comparable_path_takes_a_pure_path_in_its_own_flavour() -> None:
    assert comparable_path(PureWindowsPath(r"C:\Hive\hive.db")) == "c:/hive/hive.db"
    assert comparable_path(PurePosixPath("/a/b")) == "/a/b"


def test_hive_state_of_names_the_database_with_its_sqlite_siblings_and_the_manifest() -> None:
    state = HiveState.of(
        db=PurePosixPath("/srv/hive/hive.db"),
        secrets_dir=PurePosixPath("/srv/hive/secrets"),
        manifest=PurePosixPath("/srv/hive/hive.toml"),
    )

    assert state.files == {
        "/srv/hive/hive.db",
        "/srv/hive/hive.db-wal",
        "/srv/hive/hive.db-shm",
        "/srv/hive/hive.db-journal",
        "/srv/hive/hive.toml",
    }
    assert state.directories == {"/srv/hive/secrets"}


def test_hive_state_keeps_its_own_addresses_plain() -> None:
    state = HiveState.of(own_addresses=(ipaddress.ip_address("::ffff:192.168.1.20"),))

    assert state.own_addresses == {ipaddress.ip_address("192.168.1.20")}


def test_an_empty_hive_state_is_the_policys_default() -> None:
    policy: GuardPolicy = load_guard_policy()

    assert policy.hive_state == HiveState()
    assert HiveState.of() == HiveState()
