"""Tests for hivemind.guard.scanner.patterns: the shipped pattern file, and the loader's checks.

Roadmap step 10.6b: the patterns are data, one family per table, and every family's `examples`
are seed payloads that must fire it (the chaos seeds of 13.6 are drawn from them). The loader must
refuse anything that could make matching a hostile document backtrack without limit.

Fits into the Hive:
    Mirrors src/hivemind/guard/scanner/patterns.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.guard.scanner.patterns for the module under test.
    - hivemind.guard.defaults untrusted-content.toml for the data it loads.
"""

from __future__ import annotations

import tomllib
from importlib.resources import files

import pytest

from hivemind.guard import GuardPolicyError
from hivemind.guard.capabilities import CapabilitySet
from hivemind.guard.scanner import (
    PATTERNS_FILENAME,
    load_scan_patterns,
    score_text,
    unbounded_repetition,
)

# The six families roadmap 10.6b names, as the shipped file spells their tables.
_ROADMAP_FAMILIES = {
    "imperative",
    "role_override",
    "secret_exfiltration",
    "encoded_blob",
    "tool_call_shaped",
    "outside_hosts",
}
_BOUND = 65_536  # The default [guard.untrusted_content] max_scan_chars.


def _shipped_raw() -> dict[str, dict[str, object]]:
    """The shipped pattern file, parsed as plain TOML."""
    text = (files("hivemind.guard.defaults") / PATTERNS_FILENAME).read_text(encoding="utf-8")
    return tomllib.loads(text)


def _seeds() -> list[tuple[str, str]]:
    """Every (family, example) pair the shipped file holds."""
    return [(family.name, example) for family in load_scan_patterns().families for example in family.examples]


def test_the_shipped_file_holds_exactly_the_six_roadmap_families_each_with_seeds() -> None:
    families = load_scan_patterns().families

    assert {family.name for family in families} == _ROADMAP_FAMILIES
    assert all(family.weight > 0 and family.examples for family in families)


@pytest.mark.parametrize(("family", "example"), _seeds(), ids=lambda value: str(value)[:40])
def test_every_seed_example_fires_its_own_family(family: str, example: str) -> None:
    # An empty capability set: no host is inside the task's targets, so the host seed fires too.
    score = score_text(example, load_scan_patterns(), CapabilitySet.empty(), _BOUND)

    assert family in score.families


def test_every_shipped_pattern_repeats_only_within_a_bound() -> None:
    raw = _shipped_raw()
    patterns = [
        pattern
        for table in raw.values()
        for key in ("patterns", "anchors", "verbs")
        for pattern in table.get(key, [])  # type: ignore[attr-defined]  # a TOML list of strings
    ]

    assert patterns
    assert not [pattern for pattern in patterns if unbounded_repetition(pattern)]


@pytest.mark.parametrize("pattern", [r"a*", r"(ab)+", r"x{3,}", r"\bfoo\s*:", r"[a-z]+ bar"])
def test_unbounded_repetition_is_caught(pattern: str) -> None:
    assert unbounded_repetition(pattern)


@pytest.mark.parametrize(
    "pattern", [r"ab?c", r"x{0,40}", r"\*\+", r"[*+]{1,3}", r"a{3}", r"<\|im_start\|>"]
)
def test_bounded_or_literal_repetition_is_allowed(pattern: str) -> None:
    assert not unbounded_repetition(pattern)


def test_the_loader_refuses_an_unbounded_pattern_naming_the_source() -> None:
    text = 'bad = { kind = "patterns", weight = 1.0, description = "x", patterns = ["a.*b"] }'

    with pytest.raises(GuardPolicyError, match=r"(?s)my-patterns.*repeats without a bound"):
        load_scan_patterns(text, "my-patterns")


def test_the_loader_refuses_a_pattern_that_does_not_compile() -> None:
    text = 'bad = { kind = "patterns", weight = 1.0, description = "x", patterns = ["(unclosed"] }'

    with pytest.raises(GuardPolicyError, match="does not compile"):
        load_scan_patterns(text, "broken")


def test_the_loader_refuses_an_unknown_kind_and_bad_toml() -> None:
    unknown = 'odd = { kind = "vibes", weight = 1.0, description = "x" }'

    with pytest.raises(GuardPolicyError, match="invalid"):
        load_scan_patterns(unknown, "odd")
    with pytest.raises(GuardPolicyError, match="invalid"):
        load_scan_patterns("not [valid toml", "garbled")


def test_the_shipped_patterns_are_read_once_per_process() -> None:
    assert load_scan_patterns() is load_scan_patterns()
