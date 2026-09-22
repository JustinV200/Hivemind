"""Tests for hivemind.hive.night_veil.probe: NightVeilProbe.

Fits into the Hive:
    Mirrors src/hivemind/hive/night_veil/probe.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.night_veil.probe for the module under test.
"""

from __future__ import annotations

from hivemind.hive.night_veil.probe import NightVeilProbe
from hivemind.hive.night_veil.results import CHECK_NAMES


def test_every_check_name_is_a_protocol_method() -> None:
    for name in CHECK_NAMES:
        assert hasattr(NightVeilProbe, name), f"NightVeilProbe has no method named {name!r}."


def test_the_protocol_declares_no_extra_check_beyond_check_names() -> None:
    protocol_methods = {
        name
        for name in vars(NightVeilProbe)
        if not name.startswith("_") and callable(getattr(NightVeilProbe, name))
    }
    assert protocol_methods == set(CHECK_NAMES)
