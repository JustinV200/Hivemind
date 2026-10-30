"""Cut the Observation Hive's help pages into sections: what the help's Ask retrieves over.

The help pages are the markdown files the front end bundles (``src/views/help/content/``, design.md
section 5.12) and its build copies beside itself as ``help/<page>.md`` (ADR-0070), so the Hive
answers from exactly the words the person can read on the Help screen. A section is what the Help
screen's own search returns and what its address names: a ``##`` heading and the blocks under it
(a ``###`` heading stays inside its section), and the blocks before a page's first heading as the
page's opening section. ``slug_of`` is the front end's ``slugOf`` written in Python, so the
``section`` a passage cites is the id the Help screen gives that heading, and
``#/help/<page>?s=<section>`` lands on it. The markdown marks the grammar knows (bold, code, a
link's address) are stripped from the text, since neither a ranker nor a model needs them.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.help.retrieval``. Read
    at ``hive serve``'s start by ``hivemind.cli.compose.help``; the sections are what every
    retriever ranks and every passage cites. Calls into the standard library only.

Key invariants:
    - ``slug_of`` gives the same id as the front end's ``slugOf``: lower case, every run of other
      characters one hyphen, no hyphen at either end.
    - A page with no ``##`` heading is one section, its opening one.
    - Pure: the same text gives the same sections; the loader reads files and nothing else.

See Also:
    - packages/observation-web/src/views/help/markdown.ts and search.ts for the grammar mirrored.
    - docs/adr/0070-help-ask-answers-from-the-help-pages-on-the-cheapest-binding.md
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

HELP_PAGE_SUFFIX = ".md"  # Every help page is one markdown file; its stem is the page's id.
_HEADING = re.compile(r"^(##|###) (.+)$")  # The two heading levels the grammar knows.
_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")  # `[text](href)`: keep the words, drop the address.
_MARKS = re.compile(r"[*`]")  # Bold and code marks carry no words.
_NOT_SLUG = re.compile(r"[^a-z0-9]+")  # What a heading id turns into one hyphen.

__all__ = [
    "HELP_PAGE_SUFFIX",
    "HelpCorpus",
    "HelpSection",
    "corpus_from_pages",
    "load_help_corpus",
    "plain_words",
    "slug_of",
    "split_sections",
]


@dataclass(frozen=True, slots=True)
class HelpSection:
    """One section of one help page: what a passage cites and the Help screen can land on.

    Attributes:
        page: The page's id, the file's stem (``glossary``), as ``#/help/<page>`` names it.
        section: The heading's id (``night-veil``), or None for the page's opening section.
        heading: The heading's words; empty for the opening section, which has none.
        text: The section's words, marks stripped, a ``###`` heading's words kept in place.
    """

    page: str
    section: str | None
    heading: str
    text: str


@dataclass(frozen=True, slots=True)
class HelpCorpus:
    """Every section of every help page, in page order then reading order.

    Attributes:
        sections: The sections; empty when no page was found.
    """

    sections: tuple[HelpSection, ...]

    @property
    def is_empty(self) -> bool:
        """Whether there is nothing to answer from."""
        return not self.sections


def slug_of(text: str) -> str:
    """Return the id the front end gives a heading with these words.

    Args:
        text: The heading's words.

    Returns:
        Lower case, every run of characters outside ``a-z0-9`` one hyphen, none at either end.
    """
    return _NOT_SLUG.sub("-", text.lower()).strip("-")


def plain_words(line: str) -> str:
    """Return one line of the grammar's markdown as plain words.

    Args:
        line: A line of a help page.

    Returns:
        The line with a link reduced to its text, bold and code marks removed, a list item's
        dash dropped, and the ends trimmed.
    """
    words = _LINK.sub(r"\1", line)
    words = _MARKS.sub("", words).strip()
    return words[2:] if words.startswith("- ") else words


def split_sections(page: str, text: str) -> tuple[HelpSection, ...]:
    """Cut one page's markdown into its sections, the way the Help screen's search does.

    Args:
        page: The page's id.
        text: The page's markdown.

    Returns:
        The opening section (when the page has words before its first ``##``), then one section
        per ``##`` heading, each holding the lines under it.
    """
    sections: list[HelpSection] = []
    section_id: str | None = None
    heading = ""
    lines: list[str] = []

    def close() -> None:
        # A section with no words at all (a heading followed by the next) is still a landing
        # place, so only the opening section is dropped when empty.
        words = " ".join(line for line in lines if line)
        if section_id is not None or words:
            sections.append(HelpSection(page, section_id, heading, words))

    for raw in text.split("\n"):
        matched = _HEADING.match(raw.rstrip())
        # A `##` heading starts the next section; a `###` stays inside, its words kept.
        if matched is not None and matched.group(1) == "##":
            close()
            heading = matched.group(2).strip()
            section_id, lines = slug_of(heading), []
        elif matched is not None:
            lines.append(matched.group(2).strip())
        else:
            lines.append(plain_words(raw))
    close()
    return tuple(sections)


def corpus_from_pages(pages: Mapping[str, str]) -> HelpCorpus:
    """Build the corpus from page ids and their markdown.

    Args:
        pages: Page id to markdown, in the order the sections should keep.

    Returns:
        The corpus.
    """
    sections = [section for page, text in pages.items() for section in split_sections(page, text)]
    return HelpCorpus(tuple(sections))


def load_help_corpus(directory: Path) -> HelpCorpus:
    """Read every ``<page>.md`` under ``directory`` into a corpus; empty when there is none.

    Args:
        directory: The build's ``help/`` directory (``OBSERVATION_BUILD/help``).

    Returns:
        The corpus, pages in name order; an empty one when the directory is missing or holds no
        page, so a Hive served without a front-end build simply has no Ask.
    """
    if not directory.is_dir():
        return HelpCorpus(())
    # Name order is stable across hosts; the ranker does not care which page comes first.
    pages = {
        path.stem: path.read_text(encoding="utf-8")
        for path in sorted(directory.glob(f"*{HELP_PAGE_SUFFIX}"))
    }
    return corpus_from_pages(pages)
