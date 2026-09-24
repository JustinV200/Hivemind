"""Tests for hivemind.entrance.expose.rules: every rule has a stable code and one sentence.

Fits into the Hive:
    Mirrors src/hivemind/entrance/expose/rules.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.expose.rules for the module under test.
"""

from __future__ import annotations

import re

from hivemind.entrance.expose import ExposureRule

_CODE = re.compile(r"[a-z][a-z0-9_]*")  # A stable code: lowercase words joined by underscores.


def test_every_rule_has_a_stable_lowercase_code() -> None:
    codes = [rule.value for rule in ExposureRule]

    assert len(codes) == len(set(codes))
    assert all(_CODE.fullmatch(code) for code in codes)


def test_every_rule_has_one_sentence_without_a_trailing_full_stop() -> None:
    for rule in ExposureRule:
        assert rule.requirement
        assert not rule.requirement.endswith(".")
        assert "\n" not in rule.requirement
