"""Tests for hivemind.guard.capabilities: CapabilityFamily, Capability and CapabilitySet.

Fits into the Hive:
    Mirrors src/hivemind/guard/capabilities.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.guard.capabilities for the module under test.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hivemind.guard.capabilities import Capability, CapabilityFamily, CapabilitySet
from hivemind.guard.errors import CapabilityWideningError, InvalidCapabilityError

# One valid "family:scope" string per family, used by the round-trip and matches tests below.
_VALID_SPECS = [
    "tool:read_file",
    "tool:*",
    "fs:read:**",
    "fs:read:/scratch/notes.txt",
    "fs:write:/scratch/**",
    "net:api.example.com",
    "net:*",
    "exec:ls",
    "exec:*",
    "device:phone1",
    "device:*",
    "spend:5.00",
    "spend:0",
    "spend:*",
]


# ──────────────────────────────────────────────────────────────────────────────
# Capability: family prefixes, parsing, round trips
# ──────────────────────────────────────────────────────────────────────────────


def test_capability_family_values_are_the_family_prefixes() -> None:
    assert CapabilityFamily.TOOL.value == "tool"
    assert CapabilityFamily.FS_READ.value == "fs:read"
    assert CapabilityFamily.FS_WRITE.value == "fs:write"
    assert CapabilityFamily.NET.value == "net"
    assert CapabilityFamily.EXEC.value == "exec"
    assert CapabilityFamily.DEVICE.value == "device"
    assert CapabilityFamily.SPEND.value == "spend"


@pytest.mark.parametrize("spec", _VALID_SPECS)
def test_capability_parse_then_str_round_trips(spec: str) -> None:
    capability = Capability.parse(spec)

    assert str(capability) == spec


@pytest.mark.parametrize("spec", _VALID_SPECS)
def test_capability_str_then_parse_round_trips(spec: str) -> None:
    capability = Capability.parse(spec)

    assert Capability.parse(str(capability)) == capability


def test_capability_parse_splits_family_from_everything_after_the_prefix() -> None:
    # "fs:write" is the family; the scope keeps every remaining character, colons included, so a
    # Windows path's drive letter survives inside a scope untouched.
    capability = Capability.parse("fs:write:C:/scratch/out.txt")

    assert capability.family is CapabilityFamily.FS_WRITE
    assert capability.scope == "C:/scratch/out.txt"


@pytest.mark.parametrize(
    "spec",
    [
        "bogus:thing",  # No family named "bogus" exists.
        "fs:read",  # A known family with no colon-scope at all.
        "fs:read:",  # The colon is present but the scope after it is empty.
        "",  # Not even a family prefix.
        "spend:not-a-number",  # spend's scope must be "*" or a non-negative number.
        "spend:-1",  # spend rejects a negative amount.
    ],
)
def test_capability_parse_rejects_malformed_strings(spec: str) -> None:
    with pytest.raises(InvalidCapabilityError) as excinfo:
        Capability.parse(spec)

    assert excinfo.value.spec == spec


def test_capability_is_frozen() -> None:
    capability = Capability.parse("net:*")

    with pytest.raises(ValidationError, match="frozen"):
        capability.scope = "other"  # type: ignore[misc]  # The assignment is the test.


def test_capability_rejects_an_unknown_field() -> None:
    with pytest.raises(ValidationError, match="extra"):
        Capability.model_validate({"family": "net", "scope": "*", "extra": "nope"})


def test_capability_is_hashable() -> None:
    # CapabilitySet.capabilities is a frozenset[Capability]; this is what makes that possible.
    first = Capability.parse("net:*")
    second = Capability.parse("net:*")

    assert hash(first) == hash(second)
    assert {first, second} == {first}


# ──────────────────────────────────────────────────────────────────────────────
# Capability.matches: family gate, then per-family scope comparison
# ──────────────────────────────────────────────────────────────────────────────


def test_capability_matches_requires_the_same_family() -> None:
    held = Capability.parse("fs:read:**")
    needed = Capability.parse("fs:write:/scratch/out.txt")

    assert held.matches(needed) is False


@pytest.mark.parametrize(
    ("held_spec", "needed_spec"),
    [
        ("fs:read:**", "fs:read:/scratch/notes.txt"),
        ("fs:write:/scratch/**", "fs:write:/scratch/sub/dir/out.txt"),
        ("tool:*", "tool:read_file"),
        ("exec:*", "exec:ls"),
    ],
)
def test_capability_matches_glob_families_when_covered(held_spec: str, needed_spec: str) -> None:
    held = Capability.parse(held_spec)
    needed = Capability.parse(needed_spec)

    assert held.matches(needed) is True


def test_capability_matches_glob_families_normalises_windows_backslashes() -> None:
    # The held scope is POSIX-style; the needed scope uses Windows separators. Both are normalised
    # to forward slashes before fnmatch compares them, so this still matches.
    held = Capability.parse("fs:write:/scratch/**")
    needed = Capability(family=held.family, scope="\\scratch\\sub\\out.txt")

    assert held.matches(needed) is True


def test_capability_matches_glob_families_rejects_an_uncovered_scope() -> None:
    held = Capability.parse("fs:write:/scratch/**")
    needed = Capability.parse("fs:write:/etc/passwd")

    assert held.matches(needed) is False


def test_capability_matches_net_exact_scope() -> None:
    held = Capability.parse("net:api.example.com")

    assert held.matches(Capability.parse("net:api.example.com")) is True
    assert held.matches(Capability.parse("net:other.example.com")) is False


def test_capability_matches_net_trailing_wildcard_covers_the_prefix() -> None:
    held = Capability.parse("net:api.example.*")

    assert held.matches(Capability.parse("net:api.example.*")) is True
    assert held.matches(Capability.parse("net:api.example.com")) is True
    assert held.matches(Capability.parse("net:other.org")) is False


def test_capability_matches_device_exact_and_wildcard() -> None:
    exact = Capability.parse("device:phone1")
    wildcard = Capability.parse("device:*")

    assert exact.matches(Capability.parse("device:phone1")) is True
    assert exact.matches(Capability.parse("device:phone2")) is False
    assert wildcard.matches(Capability.parse("device:phone2")) is True


@pytest.mark.parametrize(
    ("held_amount", "needed_amount", "expected"),
    [
        ("5.00", "5.00", True),  # Exactly enough.
        ("5.00", "1.00", True),  # Held covers a smaller need.
        ("1.00", "5.00", False),  # Held falls short of what is needed.
        ("*", "1000000", True),  # A wildcard ceiling covers any numeric need.
    ],
)
def test_capability_matches_spend_by_numeric_comparison(
    held_amount: str, needed_amount: str, expected: bool
) -> None:
    held = Capability.parse(f"spend:{held_amount}")
    needed = Capability.parse(f"spend:{needed_amount}")

    assert held.matches(needed) is expected


# ──────────────────────────────────────────────────────────────────────────────
# CapabilitySet: construction, allows, attenuate, issubset
# ──────────────────────────────────────────────────────────────────────────────


def test_capability_set_empty_has_no_members() -> None:
    empty = CapabilitySet.empty()

    assert len(empty) == 0
    assert list(empty) == []


def test_capability_set_parse_builds_one_capability_per_string() -> None:
    capability_set = CapabilitySet.parse("net:*", "exec:*")

    assert len(capability_set) == 2
    assert set(capability_set) == {Capability.parse("net:*"), Capability.parse("exec:*")}


def test_capability_set_parse_rejects_a_malformed_string() -> None:
    with pytest.raises(InvalidCapabilityError):
        CapabilitySet.parse("net:*", "bogus")


def test_capability_set_allows_true_when_a_member_matches() -> None:
    capability_set = CapabilitySet.parse("fs:write:/scratch/**")

    assert capability_set.allows(Capability.parse("fs:write:/scratch/out.txt")) is True


def test_capability_set_allows_false_when_no_member_matches() -> None:
    capability_set = CapabilitySet.parse("fs:read:**")

    assert capability_set.allows(Capability.parse("fs:write:/scratch/out.txt")) is False


def test_capability_set_attenuate_returns_the_subset_unchanged_when_allowed() -> None:
    wide = CapabilitySet.parse("fs:write:/scratch/**", "net:*")
    narrow = CapabilitySet.parse("fs:write:/scratch/out.txt")

    assert wide.attenuate(narrow) is narrow


def test_capability_set_attenuate_raises_naming_the_offending_capability() -> None:
    narrow = CapabilitySet.parse("fs:read:**")
    wider = CapabilitySet.parse("fs:read:**", "fs:write:/scratch/out.txt")

    with pytest.raises(CapabilityWideningError) as excinfo:
        narrow.attenuate(wider)

    assert excinfo.value.offending == "fs:write:/scratch/out.txt"


def test_capability_set_issubset_true_when_every_capability_is_allowed() -> None:
    narrow = CapabilitySet.parse("fs:write:/scratch/out.txt")
    wide = CapabilitySet.parse("fs:write:/scratch/**")

    assert narrow.issubset(wide) is True


def test_capability_set_issubset_false_when_a_capability_is_not_allowed() -> None:
    left = CapabilitySet.parse("fs:read:**", "net:*")
    right = CapabilitySet.parse("fs:read:**")

    assert left.issubset(right) is False


def test_capability_set_has_no_union_method() -> None:
    # codingrules section 15: widening is never a method.
    assert not hasattr(CapabilitySet, "union")


def test_capability_set_is_frozen() -> None:
    capability_set = CapabilitySet.empty()

    with pytest.raises(ValidationError, match="frozen"):
        capability_set.capabilities = frozenset()  # type: ignore[misc]  # The assignment is the test.


def test_capability_set_json_round_trips() -> None:
    original = CapabilitySet.parse("fs:read:**", "net:*")

    restored = CapabilitySet.model_validate_json(original.model_dump_json())

    assert restored == original
