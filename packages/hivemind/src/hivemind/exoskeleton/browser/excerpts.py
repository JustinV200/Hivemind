"""Cut what a browser reads back, and what it says when it fails, down to bounded excerpts.

The browser fast path (the Exoskeleton's browser attachment, roadmap step 6.11) hands page content
to a model and to the flight recorder (the per-attach record of every GUI action): an
accessibility-tree snapshot, the page's visible text, one element's text. A page can be any size,
so every one of those reads is cut to a named cap here, with a note saying how long the whole was
so a reader knows it saw a part. Failures need the opposite treatment: a browser's own error text
and a crashed browser's log can quote URLs (which may carry tokens) and run to pages, while an
error message must be one short line that is safe to log (codingrules section 12), so `detail`
reduces any of them to a single printable line with every URL masked. Both browsers, the real one
and the fake, use these caps, so a clause about a bounded read holds for both.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.exoskeleton.browser`.
    Called by `browser.playwright`, `browser.fake` and `browser.launch`. Calls into the standard
    library and waggle's verdict bounds only.

Key invariants:
    - `bounded(text, limit)` is never longer than `limit`, and is `text` itself when it fits.
    - `detail` returns one line of at most MAX_DETAIL_CHARS printable characters with no URL in it.
    - REDACTED is the one spelling both browsers give a password field's value in a snapshot.

See Also:
    - hivemind.exoskeleton.browser.base for the Browser reads these caps bound.
    - .claude/codingrules.md section 12 for "never log full page contents".
"""

from __future__ import annotations

import re

from waggle.messages.capping.verdict import MAX_OBSERVED_CHARS

# About five thousand tokens: an accessibility tree a model can read in one turn. A larger page is
# cut, never paged, because the next step acts on what is visible now, not on page three.
MAX_SNAPSHOT_CHARS = 20_000
MAX_PAGE_TEXT_CHARS = 20_000  # The same budget for the page's visible text.
# One element's text is what an ELEMENT_TEXT postcondition compares, and the gate reports what it
# found in a verdict; sharing the verdict's own cap means the text always fits there whole.
MAX_ELEMENT_TEXT_CHARS = MAX_OBSERVED_CHARS
# One error's detail: enough to say what failed, never enough to carry a page.
MAX_DETAIL_CHARS = 200
# What a URL in an error detail becomes; the caller already knows the URL it used.
URL_MASK = "<url>"
REDACTED = "[redacted]"  # What a password field's value reads as wherever a read would show it.
# A URL is any scheme followed by "://" and everything up to whitespace or a quote.
_URL = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s\"'<>]*")

__all__ = [
    "MAX_DETAIL_CHARS",
    "MAX_ELEMENT_TEXT_CHARS",
    "MAX_PAGE_TEXT_CHARS",
    "MAX_SNAPSHOT_CHARS",
    "REDACTED",
    "URL_MASK",
    "bounded",
    "detail",
]


def bounded(text: str, limit: int) -> str:
    """Return `text` when it fits `limit`, else its head and a note of how long the whole was.

    Args:
        text: What was read: a snapshot, the page's text, an element's text.
        limit: The most characters the result may have; a MAX_*_CHARS constant.

    Returns:
        `text` unchanged when it fits; otherwise its first characters followed by a note such as
        "[cut: 84012 characters in all]", the note counted inside `limit`.

    Example:
        >>> bounded("abcdef", 3)
        'abc'
    """
    if len(text) <= limit:
        return text
    note = f"\n[cut: {len(text)} characters in all]"
    # A cap too small to hold the note gets a plain cut: the limit is the promise that matters.
    if len(note) >= limit:
        return text[:limit]
    return text[: limit - len(note)] + note


def detail(text: str, *, from_end: bool = False) -> str:
    """Reduce an error text or a log to one short printable line, with every URL masked.

    Args:
        text: A browser's error message, or a log a failed browser left.
        from_end: Keep the end rather than the start: a log's last lines say why it stopped,
            while an error message says what went wrong first.

    Returns:
        At most MAX_DETAIL_CHARS characters: control characters and runs of whitespace become
        single spaces, and every URL becomes URL_MASK (it may carry a token).
    """
    masked = _URL.sub(URL_MASK, text)
    printable = "".join(char if char.isprintable() else " " for char in masked)
    line = " ".join(printable.split())
    return line[-MAX_DETAIL_CHARS:] if from_end else line[:MAX_DETAIL_CHARS]
