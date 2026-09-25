"""Tests for hivemind.guard.policy.floors.hive_state: no bee touches the Hive's own state.

Fits into the Hive:
    Mirrors src/hivemind/guard/policy/floors/hive_state.py (codingrules section 3). Every case is
    decided through `evaluate`, so the floor's refusal is proven to hold whatever the bee holds.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.guard.policy.floors.hive_state for the module under test.
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md for the decision.
"""

from __future__ import annotations

import dataclasses
import ipaddress
from pathlib import PurePosixPath, PureWindowsPath

import pytest
from builders.guard import make_request

from hivemind.guard.policy import HiveState, PolicyContext, PrincipalKind, evaluate
from hivemind.guard.policy.defaults import load_guard_policy

_DB = PurePosixPath("/srv/hive/hive.db")
_STATE = HiveState.of(
    db=_DB,
    secrets_dir=PurePosixPath("/srv/hive/secrets"),
    manifest=PurePosixPath("/srv/hive/hive.toml"),
    own_addresses=(ipaddress.ip_address("192.168.1.20"),),
)
_POLICY = dataclasses.replace(load_guard_policy(), hive_state=_STATE)
_EVERYTHING = ("fs:read:**", "fs:write:**", "exec:*", "net:*")  # A bee holding every Cell effect.
_PATHS = "guard.state_floor.state_paths"
_ENTRY = "guard.state_floor.entry_points"
_LOOPBACK = "guard.state_floor.loopback"


def _rule(
    needed: str,
    *,
    kind: PrincipalKind = PrincipalKind.WORKER,
    context: PolicyContext | None = None,
) -> str:
    """Decide `needed` for a principal holding every Cell effect; return the deciding rule."""
    request = make_request(needed, *_EVERYTHING, kind=kind, context=context)
    return evaluate(request, _POLICY).rule


# ──────────────────────────────────────────────────────────────────────────────
# State paths
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "needed",
    [
        "fs:read:/srv/hive/hive.db",
        "fs:read:/srv/hive/hive.db-wal",
        "fs:read:/srv/hive/hive.db-shm",
        "fs:write:/srv/hive/hive.db-journal",
        "fs:read:/srv/hive/hive.toml",
        "fs:read:/srv/hive/secrets",
        "fs:read:/srv/hive/secrets/hive_signing_key",
        "fs:write:/srv/hive/secrets/new",
        "fs:read:/srv/hive/scratch/../hive.db",
        "fs:read:/SRV/Hive/HIVE.db",
    ],
)
def test_a_bee_is_refused_every_state_path_however_it_is_spelled(needed: str) -> None:
    assert _rule(needed) == _PATHS


def test_a_write_to_a_directory_that_holds_state_is_refused_but_a_read_is_not() -> None:
    # Replacing /srv/hive would take the database with it; listing it reads no state.
    assert _rule("fs:write:/srv/hive") == _PATHS
    assert _rule("fs:write:/") == _PATHS
    assert _rule("fs:read:/srv/hive") == "guard.held"


def test_a_pattern_whose_literal_prefix_can_reach_state_is_refused() -> None:
    assert _rule("fs:read:**") == _PATHS
    assert _rule("fs:read:/srv/hive/*.db") == _PATHS
    assert _rule("fs:read:/srv/hive/secrets/*") == _PATHS
    assert _rule("fs:read:/home/op/*.txt") == "guard.held"


def test_paths_outside_the_hives_state_are_left_to_the_held_set() -> None:
    assert _rule("fs:read:/srv/hive/scratch/notes.txt") == "guard.held"
    assert _rule("fs:write:/srv/hive/hive.db.bak/x") == "guard.held"


def test_a_windows_state_path_is_matched_in_either_separator() -> None:
    windows = HiveState.of(db=PureWindowsPath(r"C:\Hive\hive.db"))
    policy = dataclasses.replace(_POLICY, hive_state=windows)

    decision = evaluate(make_request(r"fs:read:c:\hive\HIVE.DB", "fs:read:**"), policy)

    assert decision.rule == _PATHS


# ──────────────────────────────────────────────────────────────────────────────
# Entry points
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "needed",
    [
        "exec:hive",
        "exec:/x/.venv/bin/hive",
        "exec:./hive",
        r"exec:C:\venv\Scripts\HIVE.EXE",
        "exec:hivemind-in-cell",
        "exec:/usr/local/bin/hivemind-anything",
        "exec:hiv?",
    ],
)
def test_a_bee_is_refused_every_hive_entry_point(needed: str) -> None:
    assert _rule(needed) == _ENTRY


@pytest.mark.parametrize(
    "needed", ["exec:git", "exec:hivex", "exec:/opt/beehive", "exec:hiveminder"]
)
def test_other_programs_are_left_to_the_held_set(needed: str) -> None:
    assert _rule(needed) == "guard.held"


# ──────────────────────────────────────────────────────────────────────────────
# Loopback and the Hive Stand's own addresses
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "needed",
    [
        "net:localhost",
        "net:api.localhost",
        "net:*.localhost",
        "net:127.0.0.1",
        "net:127.8.9.10",
        "net:::1",
        "net:0.0.0.0",
        "net:::",
        "net:::ffff:127.0.0.1",
        "net:169.254.169.254",
        "net:fe80::1",
        "net:192.168.1.20",
        "net:127.0.0.0/8",
        "net:*",
    ],
)
def test_a_bee_is_refused_every_loopback_spelling_and_the_stands_own_address(needed: str) -> None:
    assert _rule(needed) == _LOOPBACK


def test_a_name_that_resolved_to_loopback_is_refused_on_its_addresses() -> None:
    # `127.1` and `2130706433` are names to the grammar; the resolver says where they go.
    context = PolicyContext(resolved_addresses=(ipaddress.ip_address("127.0.0.1"),))

    assert _rule("net:127.1", context=context) == _LOOPBACK
    assert _rule("net:2130706433", context=context) == _LOOPBACK


def test_every_resolved_address_is_judged_not_only_the_first() -> None:
    addresses = (ipaddress.ip_address("93.184.216.34"), ipaddress.ip_address("192.168.1.20"))
    context = PolicyContext(resolved_addresses=addresses)

    assert _rule("net:rebind.example", context=context) == _LOOPBACK


def test_a_public_host_is_left_to_the_held_set() -> None:
    context = PolicyContext(resolved_addresses=(ipaddress.ip_address("93.184.216.34"),))

    assert _rule("net:example.com", context=context) == "guard.held"
    assert _rule("net:*.example.com") == "guard.held"
    assert _rule("net:10.0.0.0/8") == "guard.held"


# ──────────────────────────────────────────────────────────────────────────────
# Who the floor binds
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", [PrincipalKind.WARDEN, PrincipalKind.WORKER])
def test_the_floor_binds_every_bee(kind: PrincipalKind) -> None:
    assert _rule("net:localhost", kind=kind) == _LOOPBACK


@pytest.mark.parametrize(
    "kind", [PrincipalKind.OPERATOR, PrincipalKind.QUEEN, PrincipalKind.CLIENT_DEVICE]
)
def test_the_floor_leaves_principals_that_never_act_on_a_cell_to_the_held_set(
    kind: PrincipalKind,
) -> None:
    assert _rule("net:localhost", kind=kind) == "guard.held"


def test_with_no_state_named_the_fixed_parts_of_the_floor_still_hold() -> None:
    bare = load_guard_policy()  # HiveState() by default: no paths, no addresses.

    assert evaluate(make_request("exec:hive", "exec:*"), bare).rule == _ENTRY
    assert evaluate(make_request("net:127.0.0.1", "net:*"), bare).rule == _LOOPBACK
    assert evaluate(make_request("fs:read:/srv/hive/hive.db", "fs:read:**"), bare).allowed


def test_the_refusal_names_the_state_not_the_path() -> None:
    decision = evaluate(make_request("fs:read:/srv/hive/hive.db", "fs:read:**"), _POLICY)

    assert decision.reason.endswith(
        "it names the Hive's own state (its database, secret store or manifest), which no bee "
        "may read or write."
    )
