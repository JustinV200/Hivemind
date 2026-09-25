"""Tests for hivemind.guard.capabilities.families: CapabilityFamily, ScopeKind and the table.

Fits into the Hive:
    Mirrors src/hivemind/guard/capabilities/families.py (codingrules section 3: tests/unit
    mirrors src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.guard.capabilities.families for the module under test.
    - docs/adr/0039-capability-model-attenuation-and-enforcement-points.md for the table these
      tests pin.
"""

from __future__ import annotations

from types import MappingProxyType

import pytest

from hivemind.cell.tiers import CombShieldLevel
from hivemind.forage.slots import ModelSlot
from hivemind.guard.capabilities.families import (
    FAMILIES_BY_KIND,
    PARSE_ORDER,
    CapabilityFamily,
    ScopeKind,
    _index_kinds,  # White-box test of the import-time table check only; not public API.
)

# ADR-0039's table, row by row, as the strings a reviewer compares against the ADR itself.
_ADR_TABLE: dict[ScopeKind, set[str]] = {
    ScopeKind.FLAG: {
        "tool:request",
        "cell:virtual",
        "cell:hive_stand",
        "exoskeleton",
        "exoskeleton:real_display",
        "honey:write",
        "wax:propose",
        "warden:spawn",
        "forage:request",
        "question:human",
        "observe",
        "observe:thoughts",
        "entrance:submit",
        "entrance:answer",
        "entrance:push",
        "entrance:steward",
        "supersede",
        "sting_cut",
        "wifi:scan",
        "host:metadata",
    },
    ScopeKind.GLOB: {"tool", "fs:read", "fs:write", "exec", "cell:outside_scratch"},
    ScopeKind.HOST: {"net"},
    ScopeKind.PREFIX: {
        "device",
        "cell:real",
        "honey:read",
        "observe:honey",
        "geo",
        "watch",
        "tool:scope",
    },
    ScopeKind.ENUMERATED: {"llm", "cell:comb_shield", "tactic"},
    ScopeKind.ORDERED: {"honey:clearance"},
    ScopeKind.AMOUNT: {"spend"},
}


def test_families_by_kind_matches_the_adr_table_row_for_row() -> None:
    table = {
        kind: {family.value for family in families} for kind, families in FAMILIES_BY_KIND.items()
    }

    assert table == _ADR_TABLE


def test_every_family_has_exactly_the_scope_kind_its_row_names() -> None:
    for kind, values in _ADR_TABLE.items():
        for value in values:
            assert CapabilityFamily(value).scope_kind is kind


def test_phase_three_families_keep_their_names_and_values() -> None:
    # Roadmap 3.13a's seven families are stored and matched by value; renaming one would break
    # every set already written down.
    assert CapabilityFamily.TOOL.value == "tool"
    assert CapabilityFamily.FS_READ.value == "fs:read"
    assert CapabilityFamily.FS_WRITE.value == "fs:write"
    assert CapabilityFamily.NET.value == "net"
    assert CapabilityFamily.EXEC.value == "exec"
    assert CapabilityFamily.DEVICE.value == "device"
    assert CapabilityFamily.SPEND.value == "spend"


@pytest.mark.parametrize("family", list(CapabilityFamily))
def test_member_name_is_the_value_in_upper_snake(family: CapabilityFamily) -> None:
    assert family.name == family.value.upper().replace(":", "_")


def test_parse_order_is_longest_family_first() -> None:
    lengths = [len(family.value) for family in PARSE_ORDER]

    assert lengths == sorted(lengths, reverse=True)
    assert set(PARSE_ORDER) == set(CapabilityFamily)
    # The two reserved tool shapes are only safe because both are tried before `tool`.
    assert PARSE_ORDER.index(CapabilityFamily.TOOL_REQUEST) < PARSE_ORDER.index(
        CapabilityFamily.TOOL
    )


def test_llm_scope_values_are_every_model_slot_lowercased() -> None:
    assert CapabilityFamily.LLM.scope_values == tuple(slot.name.lower() for slot in ModelSlot)


def test_comb_shield_scope_values_are_every_tier_lowercased() -> None:
    assert CapabilityFamily.CELL_COMB_SHIELD.scope_values == tuple(
        level.name.lower() for level in CombShieldLevel
    )


def test_tactic_scope_values_are_the_two_mask_tactics() -> None:
    assert CapabilityFamily.TACTIC.scope_values == ("write_like_human", "mouse_like_human")


def test_honey_clearance_values_are_the_clearance_ladder_lowest_first() -> None:
    assert CapabilityFamily.HONEY_CLEARANCE.scope_values == ("c0", "c1", "c2")


def test_open_text_families_have_no_scope_values() -> None:
    assert CapabilityFamily.FS_WRITE.scope_values == ()
    assert CapabilityFamily.OBSERVE.scope_values == ()


def test_index_kinds_rejects_a_family_listed_under_two_kinds() -> None:
    doubled = MappingProxyType(
        {
            **FAMILIES_BY_KIND,
            ScopeKind.PREFIX: FAMILIES_BY_KIND[ScopeKind.PREFIX] | {CapabilityFamily.NET},
        }
    )

    with pytest.raises(AssertionError, match="'net'"):
        _index_kinds(doubled)


def test_index_kinds_rejects_a_family_with_no_kind() -> None:
    missing = MappingProxyType({**FAMILIES_BY_KIND, ScopeKind.AMOUNT: frozenset()})

    with pytest.raises(AssertionError, match="spend"):
        _index_kinds(missing)
