"""Test hivemind.entrance.help.retrieval.corpus: pages become the sections the Help screen names.

A page splits into its opening section and one per ``##`` heading, a ``###`` heading staying
inside with its words; marks and link addresses are stripped; every heading's id is the one the
front end's ``slugOf`` gives it; and the loader reads a directory of pages, or nothing.

Fits into the Hive:
    Mirrors src/hivemind/entrance/help/retrieval/corpus.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.entrance.help import PAGES

from hivemind.entrance.help.retrieval import (
    HelpSection,
    corpus_from_pages,
    load_help_corpus,
    slug_of,
    split_sections,
)


def test_a_page_splits_into_its_opening_and_one_section_per_heading() -> None:
    sections = split_sections("glossary", PAGES["glossary"])

    assert [(s.section, s.heading) for s in sections] == [
        (None, ""),
        ("night-veil", "Night Veil"),
        ("warden", "Warden"),
    ]
    assert sections[0] == HelpSection("glossary", None, "", "Every bee term, in plain language.")
    # Bold marks go, lines join, and a `###` heading stays inside its section with its words.
    assert sections[1].text.startswith("The sealed tier: a Virtual Cell behind a VPN")
    assert "What a Warden watches" in sections[2].text
    # A link keeps its words and loses its address; code marks go.
    assert "see Fleet for each one's mode." in sections[2].text
    assert "#/fleet" not in sections[2].text


@pytest.mark.parametrize(
    ("heading", "slug"),
    [("Night Veil", "night-veil"), ("Royal (C2)", "royal-c2"), ("Step-up", "step-up")],
)
def test_a_headings_id_is_the_front_ends_slug(heading: str, slug: str) -> None:
    assert slug_of(heading) == slug


def test_the_loader_reads_every_page_in_name_order_or_nothing(tmp_path: Path) -> None:
    for page, text in PAGES.items():
        (tmp_path / f"{page}.md").write_text(text, encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not a page", encoding="utf-8")

    loaded = load_help_corpus(tmp_path)
    missing = load_help_corpus(tmp_path / "absent")

    assert loaded == corpus_from_pages(PAGES)
    assert [s.page for s in loaded.sections][:3] == ["glossary", "glossary", "glossary"]
    assert missing.is_empty
