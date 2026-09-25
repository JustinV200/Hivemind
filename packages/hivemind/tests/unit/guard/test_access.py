"""Tests for hivemind.guard.access: CELL_EFFECT_FAMILIES, ceiling_for, admits and cap_to_access.

Fits into the Hive:
    Mirrors src/hivemind/guard/access.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.guard.access for the module under test.
    - .claude/codingrules.md section 14.3 for the hypothesis property-test rule the properties at
      the end of this module follow.
"""

from __future__ import annotations

from pathlib import Path, PureWindowsPath

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from hivemind.cell.tiers import AccessLevel
from hivemind.guard.access import (
    CELL_EFFECT_FAMILIES,
    admits,
    cap_to_access,
    ceiling_for,
    fill_scratch,
    governs,
)
from hivemind.guard.capabilities import Capability, CapabilityFamily, CapabilitySet, ScopeKind

# A fixed scratch root, POSIX-style so scope strings in this module read the same on every host.
_SCRATCH_ROOT = Path("/hive/scratch")

# codingrules 14.3: a generous, deterministic example budget and no per-test deadline.
_SETTINGS = settings(max_examples=200, deadline=None)


def _allows(level: AccessLevel, spec: str) -> bool:
    """Say whether `level`'s ceiling over `_SCRATCH_ROOT` allows the capability `spec`."""
    return ceiling_for(level, _SCRATCH_ROOT).allows(Capability.parse(spec))


# ──────────────────────────────────────────────────────────────────────────────
# Which families an access level governs
# ──────────────────────────────────────────────────────────────────────────────


def test_cell_effect_families_are_the_adrs_list() -> None:
    assert {family.value for family in CELL_EFFECT_FAMILIES} == {
        "fs:read",
        "fs:write",
        "exec",
        "net",
        "device",
        "cell:outside_scratch",
        "exoskeleton",
        "exoskeleton:real_display",
        "geo",
        "wifi:scan",
        "host:metadata",
    }


@pytest.mark.parametrize(
    "family",
    [
        CapabilityFamily.TOOL,  # A tool's effects are checked through the families it uses.
        CapabilityFamily.SPEND,  # Spend is bounded by grants, not by the Cell.
        CapabilityFamily.TACTIC,
        CapabilityFamily.WATCH,
        CapabilityFamily.LLM,
        CapabilityFamily.QUESTION_HUMAN,
        CapabilityFamily.HONEY_CLEARANCE,
    ],
)
def test_governs_is_false_for_a_family_that_does_not_touch_the_cell(
    family: CapabilityFamily,
) -> None:
    assert governs(family) is False


def test_governs_is_true_for_every_cell_effect_family() -> None:
    assert all(governs(family) for family in CELL_EFFECT_FAMILIES)


# ──────────────────────────────────────────────────────────────────────────────
# ceiling_for: what each level permits
# ──────────────────────────────────────────────────────────────────────────────


def test_read_only_reads_anywhere_and_nothing_else() -> None:
    assert ceiling_for(AccessLevel.READ_ONLY, _SCRATCH_ROOT).as_strings() == ("fs:read:**",)


def test_scratch_adds_scratch_writes_and_exec() -> None:
    assert _allows(AccessLevel.SCRATCH, "fs:write:/hive/scratch/sub/out.txt") is True
    assert _allows(AccessLevel.SCRATCH, "fs:write:/etc/passwd") is False
    assert _allows(AccessLevel.SCRATCH, "exec:ls") is True
    assert _allows(AccessLevel.SCRATCH, "net:api.example.com") is False
    assert _allows(AccessLevel.SCRATCH, "device:phone1") is False
    assert _allows(AccessLevel.SCRATCH, "cell:outside_scratch:/etc/x") is False


def test_full_adds_every_other_cell_effect() -> None:
    for spec in [
        "fs:write:/etc/passwd",
        "net:api.example.com",
        "device:phone1",
        "cell:outside_scratch:/etc/x",
        "exoskeleton",
        "exoskeleton:real_display",
        "geo:city",
        "wifi:scan",
        "host:metadata",
    ]:
        assert _allows(AccessLevel.FULL, spec) is True, spec


@pytest.mark.parametrize("level", list(AccessLevel))
def test_no_ceiling_holds_a_family_outside_the_cell_effects(level: AccessLevel) -> None:
    # This step's change to phase 3's ceilings: tool and spend are in none of them any more.
    ceiling = ceiling_for(level, _SCRATCH_ROOT)

    assert all(governs(capability.family) for capability in ceiling)
    assert not any(capability.family is CapabilityFamily.TOOL for capability in ceiling)
    assert not any(capability.family is CapabilityFamily.SPEND for capability in ceiling)


def test_ceilings_nest_read_only_inside_scratch_inside_full() -> None:
    read_only, scratch, full = (ceiling_for(level, _SCRATCH_ROOT) for level in AccessLevel)

    assert read_only.issubset(scratch) is True
    assert scratch.issubset(full) is True


def test_scratch_normalises_a_windows_style_scratch_root() -> None:
    ceiling = ceiling_for(AccessLevel.SCRATCH, PureWindowsPath("C:\\Users\\test\\scratch"))

    # Both a POSIX-style and a Windows-style needed scope resolve under the same ceiling.
    assert ceiling.allows(Capability.parse("fs:write:C:/Users/test/scratch/out.txt")) is True
    assert ceiling.allows(
        Capability(family=CapabilityFamily.FS_WRITE, scope="C:\\Users\\test\\scratch\\out.txt")
    )


def test_fill_scratch_never_doubles_a_slash_for_the_root() -> None:
    assert fill_scratch("fs:write:{scratch}/**", Path("/")) == "fs:write:/**"
    assert fill_scratch("fs:write:{scratch}/**", Path("/s/")) == "fs:write:/s/**"


@pytest.mark.parametrize("root", ["/lease/run*", "/lease/run?", "/lease/run[1]", "/lease/[x]*?"])
def test_a_scratch_root_with_glob_characters_matches_only_itself(root: str) -> None:
    # Unescaped, "/lease/run*/**" would grant every sibling directory the pattern happens to match.
    ceiling = ceiling_for(AccessLevel.SCRATCH, Path(root))

    assert ceiling.allows(Capability.parse(f"fs:write:{root}/out.txt")) is True
    assert ceiling.allows(Capability.parse("fs:write:/lease/runX/out.txt")) is False
    assert ceiling.allows(Capability.parse("fs:write:/lease/run1/out.txt")) is False


def test_a_set_built_from_an_escaped_root_still_attenuates_to_itself() -> None:
    # A Worker's set is filtered through its Warden's: the same escaped scope must cover itself.
    ceiling = ceiling_for(AccessLevel.SCRATCH, Path("/lease/run[1]*"))

    assert ceiling.attenuate(ceiling) == ceiling


# ──────────────────────────────────────────────────────────────────────────────
# admits: whether a level permits a family at all
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("level", "family", "expected"),
    [
        (AccessLevel.READ_ONLY, CapabilityFamily.FS_READ, True),
        (AccessLevel.READ_ONLY, CapabilityFamily.FS_WRITE, False),
        (AccessLevel.READ_ONLY, CapabilityFamily.EXEC, False),
        (AccessLevel.READ_ONLY, CapabilityFamily.TOOL, True),  # Not governed: always admitted.
        (AccessLevel.SCRATCH, CapabilityFamily.FS_WRITE, True),  # At some scope: scratch.
        (AccessLevel.SCRATCH, CapabilityFamily.NET, False),
        (AccessLevel.FULL, CapabilityFamily.HOST_METADATA, True),
    ],
)
def test_admits(level: AccessLevel, family: CapabilityFamily, expected: bool) -> None:
    assert admits(level, family) is expected


# ──────────────────────────────────────────────────────────────────────────────
# cap_to_access: narrow a requested set, leaving other families alone
# ──────────────────────────────────────────────────────────────────────────────


def test_cap_to_access_keeps_governed_entries_the_ceiling_allows() -> None:
    requested = CapabilitySet.parse(
        "fs:read:**",
        "fs:write:/hive/scratch/out.txt",  # Inside scratch: SCRATCH's ceiling allows this.
        "net:api.example.com",  # SCRATCH never permits the network.
    )

    granted = cap_to_access(requested, AccessLevel.SCRATCH, _SCRATCH_ROOT)

    assert granted == CapabilitySet.parse("fs:read:**", "fs:write:/hive/scratch/out.txt")


def test_cap_to_access_leaves_every_ungoverned_family_untouched() -> None:
    # A READ_ONLY Warden still holds question:human and llm:warden (ADR-0039).
    requested = CapabilitySet.parse(
        "question:human", "llm:warden", "tool:*", "spend:*", "tactic:*", "watch:node1", "exec:*"
    )

    granted = cap_to_access(requested, AccessLevel.READ_ONLY, _SCRATCH_ROOT)

    assert granted == CapabilitySet.parse(
        "question:human", "llm:warden", "tool:*", "spend:*", "tactic:*", "watch:node1"
    )


def test_cap_to_access_read_only_never_grants_a_write_or_effect() -> None:
    requested = CapabilitySet.parse(
        "fs:read:**",
        "fs:write:**",
        "net:*",
        "device:*",
        "exec:*",
        "cell:outside_scratch:**",
        "exoskeleton",
        "exoskeleton:real_display",
        "geo:*",
        "wifi:scan",
        "host:metadata",
    )

    granted = cap_to_access(requested, AccessLevel.READ_ONLY, _SCRATCH_ROOT)

    assert granted == CapabilitySet.parse("fs:read:**")


def test_cap_to_access_with_nothing_requested_grants_nothing() -> None:
    assert cap_to_access(CapabilitySet.empty(), AccessLevel.FULL, _SCRATCH_ROOT).as_strings() == ()


# ──────────────────────────────────────────────────────────────────────────────
# Properties: cap_to_access only ever narrows, and READ_ONLY never keeps an effect
# ──────────────────────────────────────────────────────────────────────────────

_PATHS = st.sampled_from(["**", "*", "/hive/scratch/**", "/hive/scratch/a.txt", "/etc/**", "x"])
_OPEN_TEXT = st.one_of(_PATHS, st.text(alphabet="abc/*._-", min_size=1, max_size=10))
_HOSTS = st.sampled_from(["*", "api.example.com", "*.example.com", "10.0.0.0/8", "fd00::1"])


def _scopes(family: CapabilityFamily) -> st.SearchStrategy[str]:
    """Draw a scope valid for `family`, so the strategy never hits InvalidCapabilityError."""
    match family.scope_kind:
        case ScopeKind.FLAG:
            return st.just("")
        case ScopeKind.HOST:
            return _HOSTS
        case ScopeKind.ENUMERATED:
            return st.sampled_from((*family.scope_values, "*"))
        case ScopeKind.ORDERED:
            return st.sampled_from(family.scope_values)
        case ScopeKind.AMOUNT:
            return st.sampled_from(["*", "0", "5.00", "1000000"])
        case _:
            return _OPEN_TEXT.filter(lambda scope: not scope.startswith(("request", "scope:")))


@st.composite
def _capability_sets(draw: st.DrawFn) -> CapabilitySet:
    """Draw a set of up to eight capabilities across every family."""
    members: list[Capability] = []
    for family in draw(st.lists(st.sampled_from(list(CapabilityFamily)), max_size=8)):
        members.append(Capability(family=family, scope=draw(_scopes(family))))
    return CapabilitySet(capabilities=frozenset(members))


@given(requested=_capability_sets(), level=st.sampled_from(list(AccessLevel)))
@_SETTINGS
def test_cap_to_access_result_is_always_a_subset_of_what_was_requested(
    requested: CapabilitySet, level: AccessLevel
) -> None:
    granted = cap_to_access(requested, level, _SCRATCH_ROOT)

    assert granted.capabilities <= requested.capabilities


@given(requested=_capability_sets(), level=st.sampled_from(list(AccessLevel)))
@_SETTINGS
def test_cap_to_access_governed_part_is_always_inside_the_ceiling(
    requested: CapabilitySet, level: AccessLevel
) -> None:
    ceiling = ceiling_for(level, _SCRATCH_ROOT)

    granted = cap_to_access(requested, level, _SCRATCH_ROOT)

    governed = [capability for capability in granted if governs(capability.family)]
    assert all(ceiling.allows(capability) for capability in governed)


@given(requested=_capability_sets())
@_SETTINGS
def test_cap_to_access_read_only_keeps_no_write_or_effect_family(requested: CapabilitySet) -> None:
    granted = cap_to_access(requested, AccessLevel.READ_ONLY, _SCRATCH_ROOT)

    effects = {capability.family for capability in granted if governs(capability.family)}
    assert effects <= {CapabilityFamily.FS_READ}
