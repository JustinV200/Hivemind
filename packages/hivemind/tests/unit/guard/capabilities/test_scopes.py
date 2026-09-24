"""Tests for hivemind.guard.capabilities.scopes: scope validation and matching per ScopeKind.

Fits into the Hive:
    Mirrors src/hivemind/guard/capabilities/scopes.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.guard.capabilities.scopes for the module under test.
    - tests/unit/guard/capabilities/test_hosts.py for the HOST kind's own cases.
"""

from __future__ import annotations

import pytest

from hivemind.guard.capabilities.families import CapabilityFamily
from hivemind.guard.capabilities.scopes import WILDCARD, scope_error, scope_matches

Family = CapabilityFamily


# ──────────────────────────────────────────────────────────────────────────────
# scope_error: what each kind accepts
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("family", "scope"),
    [
        (Family.OBSERVE, ""),  # A flag takes no scope at all.
        (Family.FS_WRITE, "/scratch/**"),  # A glob takes any non-empty text...
        (Family.FS_WRITE, "C:/Users/me/My Files/*"),  # ...drive letters and spaces included.
        (Family.TOOL, "read_file"),
        (Family.TOOL, "request:x"),  # Not reserved: "tool:request:x" is no longer family.
        (Family.TOOL, "scope"),  # Not reserved: only "scope:<x>" reads as tool:scope.
        (Family.DEVICE, "phone*"),  # A prefix scope may end in the wildcard.
        (Family.NET, "*.example.com"),
        (Family.LLM, "worker"),
        (Family.LLM, WILDCARD),  # "*" is accepted for every enumerated family.
        (Family.CELL_COMB_SHIELD, "night_veil"),
        (Family.TACTIC, "mouse_like_human"),
        (Family.HONEY_CLEARANCE, "c1"),
        (Family.SPEND, "5"),
        (Family.SPEND, "5.00"),
        (Family.SPEND, "0"),
        (Family.SPEND, WILDCARD),
    ],
)
def test_scope_error_accepts_a_scope_valid_for_its_kind(family: Family, scope: str) -> None:
    assert scope_error(family, scope) is None


@pytest.mark.parametrize(
    ("family", "scope", "fragment"),
    [
        (Family.OBSERVE, "foo", "takes no scope"),
        (Family.FS_READ, "", "needs a scope"),
        (Family.TOOL, "request", "reserved"),  # Would read back as the tool:request flag.
        (Family.TOOL, "scope:cell", "reserved"),  # Would read back as tool:scope:cell.
        (Family.TOOL, "scope:", "reserved"),
        (Family.NET, "10.0.0.*", "not a host"),
        (Family.LLM, "nonsense", "not one of"),
        (Family.LLM, "WORKER", "not one of"),  # Values are the lowercase member names.
        (Family.TACTIC, "type_fast", "not one of"),
        (Family.HONEY_CLEARANCE, "c3", "not one of"),
        (Family.HONEY_CLEARANCE, WILDCARD, "not one of"),  # A ladder has a top rung, not "*".
        (Family.SPEND, "-1", "non-negative"),
        (Family.SPEND, "1e3", "non-negative"),  # A plain decimal only: no exponent...
        (Family.SPEND, "inf", "non-negative"),  # ...no infinity...
        (Family.SPEND, "nan", "non-negative"),  # ...no nan...
        (Family.SPEND, " 5", "non-negative"),  # ...and no padding.
        (Family.SPEND, "5.", "non-negative"),
    ],
)
def test_scope_error_refuses_with_a_reason(family: Family, scope: str, fragment: str) -> None:
    error = scope_error(family, scope)

    assert error is not None
    assert fragment in error


# ──────────────────────────────────────────────────────────────────────────────
# scope_matches: when a held scope covers a needed one
# ──────────────────────────────────────────────────────────────────────────────


def test_scope_matches_a_flag_by_family_alone() -> None:
    assert scope_matches(Family.OBSERVE, "", "") is True


@pytest.mark.parametrize(
    ("held", "needed", "expected"),
    [
        ("**", "/scratch/notes.txt", True),
        ("/scratch/**", "/scratch/sub/dir/out.txt", True),
        ("/scratch/**", "/etc/passwd", False),
        ("/scratch/**", "\\scratch\\sub\\out.txt", True),  # Windows separators normalise.
        ("C:\\scratch\\*", "C:/scratch/out.txt", True),
    ],
)
def test_scope_matches_glob_over_posix_paths(held: str, needed: str, expected: bool) -> None:
    assert scope_matches(Family.FS_WRITE, held, needed) is expected


@pytest.mark.parametrize(
    ("held", "needed", "expected"),
    [
        ("phone1", "phone1", True),
        ("phone1", "phone2", False),
        ("phone*", "phone2", True),
        ("phone*", "phone*", True),
        ("*", "anything", True),
        ("phone1*", "phone", False),
    ],
)
def test_scope_matches_prefix(held: str, needed: str, expected: bool) -> None:
    assert scope_matches(Family.DEVICE, held, needed) is expected


def test_scope_matches_host_delegates_to_the_host_rule() -> None:
    assert scope_matches(Family.NET, "*.example.com", "api.example.com") is True
    assert scope_matches(Family.NET, "*.example.com", "badexample.com") is False


@pytest.mark.parametrize(
    ("held", "needed", "expected"),
    [
        ("worker", "worker", True),
        ("worker", "queen", False),
        (WILDCARD, "queen", True),
        (WILDCARD, WILDCARD, True),
        ("worker", WILDCARD, False),  # One slot never covers every slot.
    ],
)
def test_scope_matches_enumerated(held: str, needed: str, expected: bool) -> None:
    assert scope_matches(Family.LLM, held, needed) is expected


@pytest.mark.parametrize(
    ("held", "needed", "expected"),
    [
        ("c2", "c0", True),
        ("c2", "c2", True),
        ("c1", "c2", False),
        ("c0", "c1", False),
    ],
)
def test_scope_matches_ordered_by_rank(held: str, needed: str, expected: bool) -> None:
    assert scope_matches(Family.HONEY_CLEARANCE, held, needed) is expected


@pytest.mark.parametrize(
    ("held", "needed", "expected"),
    [
        ("5.00", "5", True),  # Exactly enough, however it is written.
        ("5.00", "1.00", True),
        ("1.00", "5.00", False),
        (WILDCARD, "1000000", True),  # No ceiling covers any amount...
        (WILDCARD, WILDCARD, True),
        ("1000000", WILDCARD, False),  # ...and only no ceiling covers no ceiling.
    ],
)
def test_scope_matches_amount_numerically(held: str, needed: str, expected: bool) -> None:
    assert scope_matches(Family.SPEND, held, needed) is expected
