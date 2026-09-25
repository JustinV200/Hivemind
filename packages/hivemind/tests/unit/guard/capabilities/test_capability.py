"""Tests for hivemind.guard.capabilities.capability: Capability, its grammar and round trips.

Fits into the Hive:
    Mirrors src/hivemind/guard/capabilities/capability.py (codingrules section 3: tests/unit
    mirrors src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.guard.capabilities.capability for the module under test.
    - .claude/codingrules.md section 14.3 for the hypothesis round-trip rule the property tests
      at the end of this module follow.
"""

from __future__ import annotations

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from hivemind.guard.capabilities.capability import Capability
from hivemind.guard.capabilities.families import CapabilityFamily, ScopeKind
from hivemind.guard.capabilities.scopes import scope_error
from hivemind.guard.errors import InvalidCapabilityError

# codingrules 14.3: a generous, deterministic example budget and no per-test deadline, since a
# slow CI host should never turn a correct property test flaky.
_SETTINGS = settings(max_examples=300, deadline=None)

# At least one valid string per ScopeKind, and the shapes the longest-first rule exists for.
_VALID_SPECS = [
    "tool:read_file",
    "tool:*",
    "tool:request",
    "tool:scope:cell",
    "tool:request:x",
    "fs:read:**",
    "fs:write:C:/scratch/out.txt",
    "exec:ls",
    "cell:outside_scratch:/home/me/**",
    "net:api.example.com",
    "net:*.example.com",
    "net:10.0.0.0/8",
    "net:fd00::1",
    "net:*",
    "device:phone1",
    "cell:real:node*",
    "honey:read:*",
    "observe:honey:hive",
    "geo:*",
    "watch:node_1",
    "llm:worker",
    "llm:*",
    "cell:comb_shield:night_veil",
    "tactic:write_like_human",
    "honey:clearance:c1",
    "spend:5.00",
    "spend:*",
    "observe",
    "observe:thoughts",
    "exoskeleton:browser",
    "exoskeleton:real_display",
    "exoskeleton:*",
    "honey:read:hive",
    "honey:read:cell:*",
    "entrance:steward",
    "sting_cut",
    "wifi:scan",
    "host:metadata",
]


# ──────────────────────────────────────────────────────────────────────────────
# parse: longest family first, flags exact, scopes valid for their kind
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("spec", _VALID_SPECS)
def test_parse_then_str_round_trips(spec: str) -> None:
    assert str(Capability.parse(spec)) == spec


@pytest.mark.parametrize("spec", _VALID_SPECS)
def test_str_then_parse_round_trips(spec: str) -> None:
    capability = Capability.parse(spec)

    assert Capability.parse(str(capability)) == capability


@pytest.mark.parametrize(
    ("spec", "family", "scope"),
    [
        ("tool:request", CapabilityFamily.TOOL_REQUEST, ""),  # Its own flag, not a tool.
        ("tool:scope:cell", CapabilityFamily.TOOL_SCOPE, "cell"),  # Its own family.
        ("tool:request:x", CapabilityFamily.TOOL, "request:x"),  # A flag matches exactly only.
        ("observe:honey:hive", CapabilityFamily.OBSERVE_HONEY, "hive"),  # Never read as observe.
        ("observe", CapabilityFamily.OBSERVE, ""),
        ("fs:write:C:/scratch/out.txt", CapabilityFamily.FS_WRITE, "C:/scratch/out.txt"),
        ("net:fd00::1", CapabilityFamily.NET, "fd00::1"),  # The scope keeps its own colons.
    ],
)
def test_parse_takes_the_longest_fitting_family(
    spec: str, family: CapabilityFamily, scope: str
) -> None:
    capability = Capability.parse(spec)

    assert capability.family is family
    assert capability.scope == scope


@pytest.mark.parametrize(
    ("spec", "reason"),
    [
        ("bogus:thing", "no capability family"),
        ("", "no capability family"),
        ("fs:read", "no capability family"),  # A scoped family with no colon-scope at all.
        ("fs:read:", "needs a scope"),
        ("tool:scope:", "needs a scope"),
        ("observe:foo", "observe takes no scope"),  # A flag is never silently widened.
        ("observe:thoughts:x", "observe:thoughts takes no scope"),
        ("honey:clearance:c3", "not one of"),
        ("llm:nonsense", "not one of"),
        ("spend:-1", "non-negative"),
        ("spend:not-a-number", "non-negative"),
        ("net:10.0.0.*", "not a host"),
        ("net:api.*", "not a host"),
    ],
)
def test_parse_rejects_malformed_strings_with_a_reason(spec: str, reason: str) -> None:
    with pytest.raises(InvalidCapabilityError) as excinfo:
        Capability.parse(spec)

    assert excinfo.value.spec == spec
    assert reason in excinfo.value.reason


# ──────────────────────────────────────────────────────────────────────────────
# Direct construction validates the same grammar
# ──────────────────────────────────────────────────────────────────────────────


def test_a_flag_family_is_built_without_a_scope() -> None:
    capability = Capability(family=CapabilityFamily.OBSERVE)

    assert capability.scope == ""
    assert str(capability) == "observe"


@pytest.mark.parametrize(
    ("family", "scope"),
    [
        (CapabilityFamily.OBSERVE, "foo"),  # A flag with a scope.
        (CapabilityFamily.NET, ""),  # A scoped family with none.
        (CapabilityFamily.TOOL, "request"),  # Reserved: it would read back as tool:request.
        (CapabilityFamily.TOOL, "scope:cell"),  # Reserved: it would read back as tool:scope.
        (CapabilityFamily.LLM, "nonsense"),
        (CapabilityFamily.SPEND, "-1"),
    ],
)
def test_direct_construction_refuses_an_invalid_scope(family: CapabilityFamily, scope: str) -> None:
    with pytest.raises(ValidationError, match="not a valid"):
        Capability(family=family, scope=scope)


def test_capability_is_frozen() -> None:
    capability = Capability.parse("net:*")

    with pytest.raises(ValidationError, match="frozen"):
        capability.scope = "other"  # type: ignore[misc]  # The assignment is the test.


def test_capability_rejects_an_unknown_field() -> None:
    with pytest.raises(ValidationError, match="extra"):
        Capability.model_validate({"family": "net", "scope": "*", "extra": "nope"})


def test_capability_is_hashable() -> None:
    # CapabilitySet.capabilities is a frozenset[Capability]; this is what makes that possible.
    first, second = Capability.parse("net:*"), Capability.parse("net:*")

    assert hash(first) == hash(second)
    assert {first, second} == {first}


def test_capability_json_round_trips() -> None:
    original = Capability.parse("tool:scope:cell")

    assert Capability.model_validate_json(original.model_dump_json()) == original


def test_capability_json_with_an_invalid_scope_is_refused() -> None:
    with pytest.raises(ValidationError):
        Capability.model_validate_json('{"family": "llm", "scope": "nonsense"}')


# ──────────────────────────────────────────────────────────────────────────────
# matches: never across families
# ──────────────────────────────────────────────────────────────────────────────


def test_matches_requires_the_same_family() -> None:
    assert Capability.parse("fs:read:**").matches(Capability.parse("fs:write:/x")) is False
    assert Capability.parse("tool:*").matches(Capability.parse("tool:request")) is False
    assert Capability.parse("observe").matches(Capability.parse("observe:thoughts")) is False


def test_matches_delegates_to_the_family_kind() -> None:
    assert Capability.parse("llm:*").matches(Capability.parse("llm:judge")) is True
    assert (
        Capability.parse("honey:clearance:c1").matches(Capability.parse("honey:clearance:c2"))
        is False
    )
    assert Capability.parse("wifi:scan").matches(Capability.parse("wifi:scan")) is True


# ──────────────────────────────────────────────────────────────────────────────
# Property: every accepted string and every constructible capability round-trips
# ──────────────────────────────────────────────────────────────────────────────

_TEXT = st.text(alphabet="abcxyz019_-./*:~ ", min_size=1, max_size=16)
_HOSTS = st.one_of(
    st.just("*"),
    st.from_regex(r"(\*\.)?[a-z0-9]{1,8}(\.[a-z0-9]{1,8}){0,2}", fullmatch=True),
    st.ip_addresses().map(str),
)
_AMOUNTS = st.one_of(
    st.just("*"),
    st.integers(min_value=0, max_value=10**8).map(str),
    st.integers(min_value=0, max_value=10**8).map(
        lambda cents: f"{cents // 100}.{cents % 100:02d}"
    ),
)


def _scopes_for(family: CapabilityFamily) -> st.SearchStrategy[str]:
    """Draw scopes of the right shape for `family`'s kind (some may still be reserved)."""
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
            return _AMOUNTS
        case _:
            return _TEXT


@st.composite
def _capabilities(draw: st.DrawFn) -> Capability:
    """Draw one constructible Capability of any family."""
    family = draw(st.sampled_from(list(CapabilityFamily)))
    scope = draw(_scopes_for(family))
    assume(scope_error(family, scope) is None)  # e.g. a random tool named exactly "request".
    return Capability(family=family, scope=scope)


@given(capability=_capabilities())
@_SETTINGS
def test_every_constructible_capability_round_trips_through_its_string(
    capability: Capability,
) -> None:
    assert Capability.parse(str(capability)) == capability


@given(
    family=st.sampled_from(list(CapabilityFamily)),
    rest=st.text(alphabet="abc019_-./*:", max_size=12),
)
@_SETTINGS
def test_every_string_parse_accepts_reads_back_exactly(family: CapabilityFamily, rest: str) -> None:
    # Strings built from a real family name followed by arbitrary text, so most of them are
    # near-misses of the grammar: whatever parse accepts must render back exactly as written.
    spec = family.value + rest
    try:
        capability = Capability.parse(spec)
    except InvalidCapabilityError:
        return
    assert str(capability) == spec


# ──────────────────────────────────────────────────────────────────────────────
# Phase 6's exoskeleton peripherals and phase 7's honey:read globs
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("scope", ["display", "real_display", "audio", "browser", "*"])
def test_exoskeleton_capabilities_parse_every_known_peripheral_and_round_trip(scope: str) -> None:
    capability = Capability.parse(f"exoskeleton:{scope}")

    assert capability.family is CapabilityFamily.EXOSKELETON
    assert str(capability) == f"exoskeleton:{scope}"


@pytest.mark.parametrize("scope", ["screen", "Display", "display,audio", "browser*"])
def test_an_unknown_exoskeleton_scope_is_refused_rather_than_granting_nothing(scope: str) -> None:
    with pytest.raises(InvalidCapabilityError):
        Capability.parse(f"exoskeleton:{scope}")


@pytest.mark.parametrize(
    ("spec", "reason"),
    [("exoskeleton", "no capability family"), ("exoskeleton:", "needs a scope")],
    ids=["no-colon", "empty-scope"],
)
def test_a_bare_exoskeleton_names_no_peripheral_and_is_refused(spec: str, reason: str) -> None:
    # Like every scoped family (`fs:read` above): the scope is what names the peripheral.
    with pytest.raises(InvalidCapabilityError, match=reason):
        Capability.parse(spec)


def test_exoskeleton_capabilities_match_exactly_and_the_wildcard_covers_every_peripheral() -> None:
    display = Capability.parse("exoskeleton:display")
    everything = Capability.parse("exoskeleton:*")

    assert display.matches(Capability.parse("exoskeleton:display"))
    # A lease-started display is not the operator's own screen: holding one never grants the other.
    assert not display.matches(Capability.parse("exoskeleton:real_display"))
    assert everything.matches(Capability.parse("exoskeleton:real_display"))
    assert not everything.matches(Capability.parse("device:display"))


@pytest.mark.parametrize(
    ("held_spec", "needed_spec", "expected"),
    [
        ("honey:read:*", "honey:read:hive", True),
        ("honey:read:cell:*", "honey:read:cell:c_123", True),
        ("honey:read:hive", "honey:read:task:t_1", False),
    ],
)
def test_honey_read_globs_a_honey_store_scope(
    held_spec: str, needed_spec: str, expected: bool
) -> None:
    held = Capability.parse(held_spec)

    assert held.matches(Capability.parse(needed_spec)) is expected
