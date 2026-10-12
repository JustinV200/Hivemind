"""Tests for hivemind.workers.tools.comb.entry: entries, the package source and the bundle.

Fits into the Hive:
    Mirrors src/hivemind/workers/tools/comb/entry.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.tools.comb.entry for the module under test.
"""

from __future__ import annotations

from builders.comb_tools import make_comb_package, make_promoted
from builders.royal_jelly import make_offer, make_tool_spec

from hivemind.royal_jelly.registry import InMemoryCombRegistry, QueenPrincipal
from hivemind.workers.tools.comb import CombToolBundle, CombToolEntry
from waggle.clock import FakeClock


async def test_from_row_reads_the_registry_row_and_its_stored_spec() -> None:
    clock = FakeClock()
    spec = make_tool_spec(clock, name="disk_report", required_capabilities=("fs:read:**",))
    report, offer = make_offer(spec)
    change = await InMemoryCombRegistry(clock).promote(
        spec, report, offer, QueenPrincipal(), "because"
    )

    entry = CombToolEntry.from_row(change.row)

    assert (entry.name, entry.version, entry.tool_id) == ("disk_report", "1.0.0", spec.tool_id)
    assert entry.description == spec.description
    assert entry.input_schema_json == spec.input_schema_json
    assert entry.required_capabilities == ("fs:read:**",)
    assert entry.supported_os == spec.supported_os
    assert entry.package_sha256 == offer.package_sha256
    assert entry.package_size_bytes == offer.package_size_bytes
    assert (entry.scope, entry.cell_id, entry.entry_point) == (spec.scope, None, "tool:run")


def test_from_promoted_reads_the_announcement() -> None:
    package = make_comb_package(name="disk_report")

    entry = CombToolEntry.from_promoted(make_promoted(package))

    assert entry == package.entry


def test_the_bundle_replace_swaps_the_whole_set_and_bumps_the_version() -> None:
    first, second = make_comb_package(name="first_tool"), make_comb_package(name="second_tool")
    bundle = CombToolBundle([first.entry])
    assert bundle.version == 0

    bundle.replace([second.entry])

    assert bundle.entries() == (second.entry,)
    assert bundle.version == 1


def test_replacing_with_the_same_entries_is_a_no_op() -> None:
    package = make_comb_package()
    bundle = CombToolBundle([package.entry])

    bundle.replace([package.entry])

    assert bundle.version == 0


def test_an_empty_bundle_has_no_entries_and_its_own_loader() -> None:
    a, b = CombToolBundle(), CombToolBundle()

    assert a.entries() == () and a.loader is not b.loader
