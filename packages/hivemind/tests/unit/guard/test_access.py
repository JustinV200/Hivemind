"""Tests for hivemind.guard.access: ceiling_for and cap_to_access.

Fits into the Hive:
    Mirrors src/hivemind/guard/access.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.guard.access for the module under test.
    - .claude/codingrules.md section 14.3 for the hypothesis property-test rule
      test_cap_to_access_never_exceeds_the_ceiling follows.
"""

from __future__ import annotations

from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from hivemind.cell.tiers import AccessLevel
from hivemind.guard.access import cap_to_access, ceiling_for
from hivemind.guard.capabilities import Capability, CapabilityFamily, CapabilitySet

# A fixed scratch root, POSIX-style so scope strings in this module read the same on every host.
_SCRATCH_ROOT = Path("/hive/scratch")

# codingrules 14.3: hypothesis property tests get a generous, deterministic example budget and no
# per-test deadline, since a slow CI host should never turn a correct test flaky.
_SETTINGS = settings(max_examples=100, deadline=None)


# ──────────────────────────────────────────────────────────────────────────────
# ceiling_for: what each AccessLevel permits
# ──────────────────────────────────────────────────────────────────────────────


def test_ceiling_for_read_only_allows_reading_anywhere() -> None:
    ceiling = ceiling_for(AccessLevel.READ_ONLY, _SCRATCH_ROOT)

    assert ceiling.allows(Capability.parse("fs:read:/anywhere/at/all.txt")) is True


def test_ceiling_for_read_only_denies_write_exec_net_device_spend() -> None:
    ceiling = ceiling_for(AccessLevel.READ_ONLY, _SCRATCH_ROOT)

    assert ceiling.allows(Capability.parse("fs:write:/hive/scratch/out.txt")) is False
    assert ceiling.allows(Capability.parse("exec:ls")) is False
    assert ceiling.allows(Capability.parse("tool:read_file")) is False
    assert ceiling.allows(Capability.parse("net:api.example.com")) is False
    assert ceiling.allows(Capability.parse("device:phone1")) is False
    assert ceiling.allows(Capability.parse("spend:1.00")) is False


def test_ceiling_for_scratch_allows_writes_inside_the_scratch_root() -> None:
    ceiling = ceiling_for(AccessLevel.SCRATCH, _SCRATCH_ROOT)

    assert ceiling.allows(Capability.parse("fs:write:/hive/scratch/sub/out.txt")) is True


def test_ceiling_for_scratch_denies_writes_outside_the_scratch_root() -> None:
    ceiling = ceiling_for(AccessLevel.SCRATCH, _SCRATCH_ROOT)

    assert ceiling.allows(Capability.parse("fs:write:/etc/passwd")) is False


def test_ceiling_for_scratch_allows_any_exec_and_any_tool() -> None:
    ceiling = ceiling_for(AccessLevel.SCRATCH, _SCRATCH_ROOT)

    assert ceiling.allows(Capability.parse("exec:ls")) is True
    assert ceiling.allows(Capability.parse("tool:read_file")) is True


def test_ceiling_for_scratch_denies_net_device_spend() -> None:
    ceiling = ceiling_for(AccessLevel.SCRATCH, _SCRATCH_ROOT)

    assert ceiling.allows(Capability.parse("net:api.example.com")) is False
    assert ceiling.allows(Capability.parse("device:phone1")) is False
    assert ceiling.allows(Capability.parse("spend:1.00")) is False


def test_ceiling_for_full_allows_writes_anywhere_and_net_device_spend() -> None:
    ceiling = ceiling_for(AccessLevel.FULL, _SCRATCH_ROOT)

    assert ceiling.allows(Capability.parse("fs:write:/etc/passwd")) is True
    assert ceiling.allows(Capability.parse("net:api.example.com")) is True
    assert ceiling.allows(Capability.parse("device:phone1")) is True
    assert ceiling.allows(Capability.parse("spend:1000000")) is True


def test_ceiling_for_nests_read_only_inside_scratch_inside_full() -> None:
    read_only = ceiling_for(AccessLevel.READ_ONLY, _SCRATCH_ROOT)
    scratch = ceiling_for(AccessLevel.SCRATCH, _SCRATCH_ROOT)
    full = ceiling_for(AccessLevel.FULL, _SCRATCH_ROOT)

    assert read_only.issubset(scratch) is True
    assert scratch.issubset(full) is True
    assert read_only.issubset(full) is True


def test_ceiling_for_scratch_normalises_a_windows_style_scratch_root() -> None:
    windows_root = Path("C:\\Users\\test\\scratch")

    ceiling = ceiling_for(AccessLevel.SCRATCH, windows_root)

    # Both a POSIX-style and a Windows-style needed scope resolve under the same ceiling.
    assert ceiling.allows(Capability.parse("fs:write:C:/Users/test/scratch/out.txt")) is True
    assert (
        ceiling.allows(
            Capability(family=CapabilityFamily.FS_WRITE, scope="C:\\Users\\test\\scratch\\out.txt")
        )
        is True
    )


# ──────────────────────────────────────────────────────────────────────────────
# cap_to_access: narrow a requested set down to what the ceiling allows
# ──────────────────────────────────────────────────────────────────────────────


def test_cap_to_access_keeps_only_what_the_ceiling_allows() -> None:
    requested = CapabilitySet.parse(
        "fs:read:**",
        "fs:write:/hive/scratch/out.txt",  # Inside scratch: SCRATCH's ceiling allows this.
        "net:api.example.com",  # Not part of SCRATCH's ceiling at all.
    )

    granted = cap_to_access(requested, AccessLevel.SCRATCH, _SCRATCH_ROOT)

    assert granted == CapabilitySet.parse("fs:read:**", "fs:write:/hive/scratch/out.txt")


def test_cap_to_access_read_only_never_grants_a_write_capability() -> None:
    # codingrules section 8.7: "a READ_ONLY device can never receive a write capability however
    # the policy is configured" -- requested asks for FULL-style write/net/spend access anyway.
    requested = CapabilitySet.parse(
        "fs:read:**", "fs:write:**", "net:*", "device:*", "spend:*", "exec:*", "tool:*"
    )

    granted = cap_to_access(requested, AccessLevel.READ_ONLY, _SCRATCH_ROOT)

    assert granted == CapabilitySet.parse("fs:read:**")


def test_cap_to_access_with_nothing_requested_grants_nothing() -> None:
    granted = cap_to_access(CapabilitySet.empty(), AccessLevel.FULL, _SCRATCH_ROOT)

    assert granted == CapabilitySet.empty()


# ──────────────────────────────────────────────────────────────────────────────
# Property: cap_to_access never widens past the level's own ceiling
# ──────────────────────────────────────────────────────────────────────────────

_SCOPE_ALPHABET = st.characters(
    whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters="/*.-_~"
)


def _scope_strategy(family: CapabilityFamily) -> st.SearchStrategy[str]:
    """Build scope strings valid for `family`, so hypothesis never hits InvalidCapabilityError."""
    if family is CapabilityFamily.SPEND:
        amounts = st.floats(min_value=0, max_value=1_000_000, allow_nan=False, allow_infinity=False)
        return st.one_of(st.just("*"), amounts.map(lambda amount: f"{amount:.2f}"))
    return st.text(alphabet=_SCOPE_ALPHABET, min_size=1, max_size=24)


@st.composite
def _capabilities(draw: st.DrawFn) -> Capability:
    """Draw one random, always-valid Capability across every family."""
    family = draw(st.sampled_from(list(CapabilityFamily)))
    scope = draw(_scope_strategy(family))
    return Capability(family=family, scope=scope)


@st.composite
def _capability_sets(draw: st.DrawFn) -> CapabilitySet:
    """Draw a random CapabilitySet of up to six members, possibly spanning several families."""
    members = draw(st.lists(_capabilities(), max_size=6))
    return CapabilitySet(capabilities=frozenset(members))


@given(requested=_capability_sets(), level=st.sampled_from(list(AccessLevel)))
@_SETTINGS
def test_cap_to_access_never_exceeds_the_ceiling(
    requested: CapabilitySet, level: AccessLevel
) -> None:
    granted = cap_to_access(requested, level, _SCRATCH_ROOT)

    assert granted.issubset(ceiling_for(level, _SCRATCH_ROOT))
