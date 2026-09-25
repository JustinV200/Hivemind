"""Tests for hivemind.pheromone.events.families.honey: the Honey Store's event family.

Fits into the Hive:
    Mirrors src/hivemind/pheromone/events/families/honey.py (codingrules section 3: tests/unit
    mirrors src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.pheromone.events.families.honey for the module under test.
    - test_codec.py beside this module for the tests every family shares.
"""

from __future__ import annotations

from hivemind.pheromone.events.families import EVENT_FAMILIES, HoneyEvent


def test_honey_family_kinds_match_the_documented_vocabulary() -> None:
    # roadmap phase 7 (ADR-0035): the Honey Store's own intake, ripening, labelling and query
    # events; the module docstring's `honey` entry is the source of truth this pins.
    assert {
        "honey.nectar_received",
        "honey.nectar_deduplicated",
        "honey.nectar_rejected",
        "honey.ripened",
        "honey.ripen_failed",
        "honey.reembedded",
        "honey.label_raised",
        "honey.label_lowered",
        "honey.retired",
        "honey.queried",
        "honey.note_proposed",
        "honey.vectors_pruned",
    } == HoneyEvent.KINDS


def test_the_codec_registers_the_honey_family() -> None:
    assert EVENT_FAMILIES["honey"] is HoneyEvent
