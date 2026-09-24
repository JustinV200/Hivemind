"""Count how often one pattern family fires in a normalised text: four detectors, all linear.

The untrusted-content scanner's families (roadmap step 10.6b) are data, but each needs a way of
matching: a list of patterns, two lists that must fire near each other, a long encoded run, or a
URL host outside what the reading bee may reach. Each is a small detector here with one method,
`count`, returning how many times its family fired, stopping at the family's `max_hits`. Every one
is linear in the text: data patterns carry only bounded repetitions (`hivemind.guard.scanner.
patterns` refuses the rest), and the three code-level expressions below each match a single
character class greedily with nothing after it, which a backtracking engine settles in one pass.
`normalise` is the one preparation every detector reads: NFKC folding (full-width letters become
plain ones), zero-width characters removed (so "ig​nore" is "ignore"), carriage returns made
newlines and runs of other blanks collapsed to one space, so a pattern can spell a phrase with
single spaces and still meet it split by tabs or padding.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.guard.scanner`. Built
    by `hivemind.guard.scanner.patterns` from the pattern file; called by `hivemind.guard.scanner.
    score.score_text`. Calls into `hivemind.guard.capabilities` (a `net` capability and its host
    grammar, to test a URL's host against the reader's own set) and the standard library only.

Key invariants:
    - No detector ever returns more than the `limit` it is given, and none keeps any matched text.
    - A host detector with no targets (`None`) never fires: text with no task to measure against
      (a human's chat message) has no "outside".
    - Matching never resolves a name or touches the network.

See Also:
    - hivemind.guard.scanner.patterns for the families these detectors are built from.
    - hivemind.guard.capabilities.hosts for the `net` host grammar the host detector reuses.
    - docs/guard/untrusted-content.md for what each family catches and why.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Protocol

from hivemind.guard.capabilities import Capability, CapabilityFamily, CapabilitySet
from hivemind.guard.capabilities.hosts import host_error

# Code points that render as nothing but split a word for a naive matcher: zero-width space,
# non-joiner and joiner, the word joiner, the byte-order mark and the soft hyphen.
_INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿­"))
# Any blank that is not a line break, one or more: collapsed to one space. A single class matched
# greedily with nothing after it is linear, which is why this is code and not data.
_BLANKS = re.compile(r"[^\S\n]+")
# A run of base64 (and so hex) characters, line breaks included so a wrapped blob stays one run.
_ENCODED_RUN = re.compile(r"[A-Za-z0-9+/=_\n-]+")
# A URL's host after its scheme and any user part: bounded pieces only, so matching stays linear.
_URL_HOST = re.compile(
    r"\b(?:https?|ftps?|wss?|sftp|ssh)://(?:[^\s/@]{1,64}@)?(\[[0-9a-f:.]{2,45}\]|[a-z0-9.-]{1,253})",
    re.IGNORECASE,
)
# The shortest line a wrapped encoding uses (PEM wraps at 64, MIME at 76): a run whose lines are
# shorter is a list of words one per line, measured line by line instead of as one blob.
_MIN_WRAPPED_LINE = 40
_MAX_PROXIMITY_MARKS = 32  # Positions kept per list; beyond this a text has already fired anyway.
_MAX_URLS = 64  # URLs examined per text; more distinct hosts than this change nothing.

__all__ = [
    "Detector",
    "HostDetector",
    "PatternDetector",
    "ProximityDetector",
    "RunDetector",
    "normalise",
]


class Detector(Protocol):
    """Count how often one family fires in a normalised text, up to a limit."""

    def count(self, text: str, targets: CapabilitySet | None, limit: int) -> int:
        """Return how many times this family fires in `text`, never more than `limit`.

        Args:
            text: The normalised, already bounded text (`normalise`).
            targets: The reading bee's own capability set, whose `net` scopes are the hosts its
                task may reach; None when the text has no task to measure against.
            limit: The family's `max_hits`; counting stops there.

        Returns:
            A count between 0 and `limit`.
        """
        ...


def normalise(text: str) -> str:
    """Fold `text` to the form every detector matches: NFKC, no invisibles, single blanks.

    Args:
        text: The bounded text to prepare (the caller has already cut it to the scanner's bound).

    Returns:
        The folded text, line breaks kept (patterns may anchor on a line start).
    """
    folded = unicodedata.normalize("NFKC", text).translate(_INVISIBLE)
    # One newline convention, so `^` means the same thing whatever system wrote the text.
    unified = folded.replace("\r\n", "\n").replace("\r", "\n")
    return _BLANKS.sub(" ", unified)


@dataclass(frozen=True, slots=True)
class PatternDetector:
    """Fire once per match of any of a family's patterns."""

    patterns: tuple[re.Pattern[str], ...]  # Compiled case-insensitive and per line.

    def count(self, text: str, targets: CapabilitySet | None, limit: int) -> int:
        """Count matches across every pattern, stopping at `limit`; see `Detector.count`."""
        del targets  # A pattern family reads the text alone.
        found = 0
        # Each pattern's matches in turn; the family's own cap ends the walk early.
        for pattern in self.patterns:
            for _match in pattern.finditer(text):
                found += 1
                if found >= limit:
                    return limit
        return found


@dataclass(frozen=True, slots=True)
class ProximityDetector:
    """Fire when an anchor (a secret's path) and a verb (a way out) sit within a window."""

    anchors: tuple[re.Pattern[str], ...]  # What must not leave: key files, credential paths.
    verbs: tuple[re.Pattern[str], ...]  # How it would leave: curl, upload, send, ...
    window_chars: int  # The most characters between an anchor's start and a verb's start.

    def count(self, text: str, targets: CapabilitySet | None, limit: int) -> int:
        """Count anchors with a verb inside the window, stopping at `limit`; see `Detector`."""
        del targets  # Proximity reads the text alone.
        anchors = _positions(self.anchors, text)
        verbs = _positions(self.verbs, text)
        found = 0
        # Every anchor is checked against every verb: both lists are capped, so this stays small.
        for anchor in anchors:
            if any(abs(anchor - verb) <= self.window_chars for verb in verbs):
                found += 1
                if found >= limit:
                    return limit
        return found


@dataclass(frozen=True, slots=True)
class RunDetector:
    """Fire once per unbroken base64 or hex run of at least `min_chars` characters."""

    min_chars: int  # The run length, line breaks excluded, at which a run counts as a blob.
    min_distinct: int  # Distinct characters a run needs, so "-----" or "=====" never counts.

    def count(self, text: str, targets: CapabilitySet | None, limit: int) -> int:
        """Count long, varied encoded runs, stopping at `limit`; see `Detector.count`."""
        del targets  # A run is a property of the text alone.
        found = 0
        # Each maximal run is visited once; a short or monotonous one is skipped cheaply.
        for match in _ENCODED_RUN.finditer(text):
            run = match.group()
            if _blob_length(run) >= self.min_chars and len(set(run) - {"\n"}) >= self.min_distinct:
                found += 1
                if found >= limit:
                    return limit
        return found


@dataclass(frozen=True, slots=True)
class HostDetector:
    """Fire once per distinct URL host the reading bee holds no `net` capability for."""

    def count(self, text: str, targets: CapabilitySet | None, limit: int) -> int:
        """Count distinct outside hosts, stopping at `limit`; see `Detector.count`."""
        if targets is None:
            return 0  # No task to measure against: nothing is outside (module docstring).
        outside: set[str] = set()
        # Each URL's host once; an unparseable host is outside by definition, since no net
        # capability can name it.
        for index, match in enumerate(_URL_HOST.finditer(text)):
            if index >= _MAX_URLS:
                break
            host = match.group(1).strip("[]").rstrip(".").lower()
            if host not in outside and not _reachable(host, targets):
                outside.add(host)
                if len(outside) >= limit:
                    return limit
        return len(outside)


def _positions(patterns: tuple[re.Pattern[str], ...], text: str) -> list[int]:
    """Return the start of every match of `patterns` in `text`, at most the proximity cap."""
    marks: list[int] = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            marks.append(match.start())
            if len(marks) >= _MAX_PROXIMITY_MARKS:
                return marks
    return marks


def _blob_length(run: str) -> int:
    """Return how long `run` is as one encoded blob: joined if wrapped, else its longest line.

    A wrapped encoding keeps every line but the last at a fixed, long width; a run of short lines
    (a directory listing, one word per line) is not one blob, so only its longest line counts.
    """
    segments = [segment for segment in run.split("\n") if segment]
    if all(len(segment) >= _MIN_WRAPPED_LINE for segment in segments[:-1]):
        return sum(len(segment) for segment in segments)
    return max((len(segment) for segment in segments), default=0)


def _reachable(host: str, targets: CapabilitySet) -> bool:
    """Return whether `targets` holds a `net` capability covering `host` (ADR-0031's grammar)."""
    if host_error(host) is not None:
        return False  # Not a host the grammar can name, so no capability can cover it.
    return targets.allows(Capability(family=CapabilityFamily.NET, scope=host))
