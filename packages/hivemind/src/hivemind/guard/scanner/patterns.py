"""Load the untrusted-content patterns: the shipped TOML, checked, compiled into detectors.

The scanner's patterns are data, not code (roadmap step 10.6b): one family per table in
`hivemind.guard.defaults/untrusted-content.toml`, read through `importlib.resources` so a wheel
and a checkout agree, exactly as the Guard policy is. A family's `kind` picks its detector
(`patterns`, `proximity`, `run` or `hosts`, `hivemind.guard.scanner.detectors`); its `weight` is
what it adds to a text's score each time it fires, at most `max_hits` times; its `examples` are
seed payloads that must fire it (a test holds the file to that, and the chaos seeds of roadmap
13.6 are drawn from them). Every pattern is checked before it is compiled: it must compile, and
every repetition in it must be bounded (`?` and `{m,n}` only, never `*`, `+` or `{n,}`), so no
entry in the file can make matching a hostile document backtrack without limit. Patterns compile
case-insensitive and per line (`^` is a line start).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.guard.scanner`. Called
    by `hivemind.guard.scanner.scanner` (the default scanner) and by composition roots building a
    configured one. Calls into `hivemind.guard.errors` (GuardPolicyError), this package's
    `detectors` and the standard library (`tomllib`, `importlib.resources`, `re`).

Key invariants:
    - `load_scan_patterns` either returns fully checked, compiled patterns or raises
      `GuardPolicyError` naming the source and the offending entry; never a partial set.
    - Every compiled pattern has only bounded repetitions (`unbounded_repetition` is False).
    - The shipped file is read once per process (`functools.cache`); the result is immutable.

See Also:
    - hivemind.guard.defaults for untrusted-content.toml, the shipped patterns.
    - hivemind.guard.policy.defaults for load_guard_policy, the same shipped-data pattern.
    - docs/guard/untrusted-content.md for every family, explained.
"""

from __future__ import annotations

import functools
import re
import tomllib
from dataclasses import dataclass
from importlib.resources import files
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError

from hivemind.guard.errors import GuardPolicyError
from hivemind.guard.scanner.detectors import (
    Detector,
    HostDetector,
    PatternDetector,
    ProximityDetector,
    RunDetector,
)
from hivemind.guard.scanner.verdict import MAX_FAMILY_NAME_CHARS

PATTERNS_FILENAME = "untrusted-content.toml"  # The shipped patterns, inside _DEFAULTS_PACKAGE.
_DEFAULTS_PACKAGE = "hivemind.guard.defaults"  # Addressed by dotted name: works from a wheel.
_FLAGS = re.IGNORECASE | re.MULTILINE  # Every pattern: case-insensitive, `^` at each line start.
MAX_WEIGHT = 100.0  # A family weight is a small number; anything larger is a typo.
MAX_HITS = 16  # How often one family may add its weight to one text, at most.
MAX_WINDOW_CHARS = 4_096  # A proximity window wider than this is no longer "beside".
MAX_RUN_CHARS = 65_536  # A run threshold beyond the scanner's own default bound never fires.
# Escapes first (so `\*` is literal), then character classes (where `*` and `+` are literal),
# then what is left must hold no `*`, no `+` and no open-ended `{n,}` repetition.
_ESCAPE = re.compile(r"\\.", re.DOTALL)
_CHARACTER_CLASS = re.compile(r"\[\^?\]?[^\]]{0,256}\]")
_OPEN_RANGE = re.compile(r"\{\d{0,6},\}")

__all__ = [
    "MAX_HITS",
    "MAX_RUN_CHARS",
    "MAX_WEIGHT",
    "MAX_WINDOW_CHARS",
    "PATTERNS_FILENAME",
    "CompiledFamily",
    "CompiledPatterns",
    "HostFamily",
    "PatternFamily",
    "ProximityFamily",
    "RunFamily",
    "ScanPatterns",
    "load_scan_patterns",
    "unbounded_repetition",
]


def unbounded_repetition(pattern: str) -> bool:
    """Return whether `pattern` repeats anything without an upper bound (`*`, `+` or `{n,}`).

    Args:
        pattern: A regular expression as the pattern file spells it.

    Returns:
        True when the pattern holds an unbounded repetition outside a character class.
    """
    bare = _CHARACTER_CLASS.sub("", _ESCAPE.sub("", pattern))
    return "*" in bare or "+" in bare or _OPEN_RANGE.search(bare) is not None


def _checked_pattern(pattern: str) -> str:
    """Refuse a pattern that does not compile or repeats without a bound (module docstring)."""
    try:
        re.compile(pattern, _FLAGS)
    except re.error as exc:
        raise ValueError(f"pattern {pattern!r} does not compile: {exc}") from exc
    if unbounded_repetition(pattern):
        raise ValueError(
            f"pattern {pattern!r} repeats without a bound; use ? or {{m,n}}, never *, + or {{n,}}"
        )
    return pattern


_Pattern = Annotated[str, Field(min_length=1), AfterValidator(_checked_pattern)]
_FamilyName = Annotated[str, Field(pattern=rf"^[a-z][a-z_]{{0,{MAX_FAMILY_NAME_CHARS - 1}}}$")]
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


class _FamilyFields(BaseModel):
    """What every family table carries, whatever its kind."""

    model_config = _MODEL_CONFIG

    weight: float = Field(gt=0, le=MAX_WEIGHT, description="Added to the score per hit.")
    max_hits: int = Field(default=1, ge=1, le=MAX_HITS, description="Hits that add weight.")
    description: str = Field(min_length=1, description="What the family catches, in one line.")
    examples: tuple[str, ...] = Field(
        default=(), description="Seed payloads that must fire this family (chaos seeds, 13.6)."
    )


class PatternFamily(_FamilyFields):
    """A family that fires once per match of any of its patterns."""

    kind: Literal["patterns"] = Field(description="Matched by `PatternDetector`.")
    patterns: tuple[_Pattern, ...] = Field(min_length=1, description="Bounded regexes.")


class ProximityFamily(_FamilyFields):
    """A family that fires when an anchor and a verb match within `window_chars`."""

    kind: Literal["proximity"] = Field(description="Matched by `ProximityDetector`.")
    anchors: tuple[_Pattern, ...] = Field(min_length=1, description="What must not leave.")
    verbs: tuple[_Pattern, ...] = Field(min_length=1, description="How it would leave.")
    window_chars: int = Field(ge=1, le=MAX_WINDOW_CHARS, description="Most chars between them.")


class RunFamily(_FamilyFields):
    """A family that fires on a long, varied run of base64 or hex characters."""

    kind: Literal["run"] = Field(description="Matched by `RunDetector`.")
    min_chars: int = Field(ge=16, le=MAX_RUN_CHARS, description="The run length that counts.")
    min_distinct: int = Field(ge=2, le=64, description="Distinct characters a run needs.")


class HostFamily(_FamilyFields):
    """A family that fires on URL hosts outside the reading bee's `net` capabilities."""

    kind: Literal["hosts"] = Field(description="Matched by `HostDetector`.")


_Family = Annotated[
    PatternFamily | ProximityFamily | RunFamily | HostFamily, Field(discriminator="kind")
]


class ScanPatterns(BaseModel):
    """The whole pattern file: one table per family, keyed by the family's name."""

    model_config = _MODEL_CONFIG

    families: dict[_FamilyName, _Family] = Field(
        min_length=1, description="Every family, keyed by its table name."
    )


@dataclass(frozen=True, slots=True)
class CompiledFamily:
    """One family, ready to score: its name, weight, hit cap, detector and seed examples."""

    name: str  # The table name; recorded on a verdict when the family fires.
    weight: float  # Added to the score per hit.
    max_hits: int  # Hits past this add nothing.
    detector: Detector  # How the family matches (hivemind.guard.scanner.detectors).
    examples: tuple[str, ...]  # Seed payloads that must fire this family.


@dataclass(frozen=True, slots=True)
class CompiledPatterns:
    """Every family of one pattern file, compiled; immutable, so one copy serves a process."""

    families: tuple[CompiledFamily, ...]  # In the file's own order.
    source: str  # Where they were read from, for error messages and the docs.


def load_scan_patterns(text: str | None = None, source: str = "") -> CompiledPatterns:
    """Read, check and compile a pattern file: the shipped one, or `text` in its shape.

    Args:
        text: A pattern file's TOML text; None reads the file shipped in `hivemind.guard.defaults`
            (cached once per process).
        source: A label for `text` in error messages; ignored when `text` is None.

    Returns:
        The compiled families, in the file's own order.

    Raises:
        GuardPolicyError: The text is not TOML, not the pattern file's shape, or a pattern does
            not compile or repeats without a bound. The message names the source and the entry.
    """
    if text is None:
        return _shipped_patterns()
    return _compile(text, source or "the given pattern text")


@functools.cache
def _shipped_patterns() -> CompiledPatterns:
    """Read the shipped pattern file once per process (it never changes at runtime)."""
    source = f"the shipped {PATTERNS_FILENAME}"
    try:
        text = (files(_DEFAULTS_PACKAGE) / PATTERNS_FILENAME).read_text(encoding="utf-8")
    except OSError as exc:
        raise GuardPolicyError(f"Could not read {source}: {exc}") from exc
    return _compile(text, source)


def _compile(text: str, source: str) -> CompiledPatterns:
    """Parse, validate and compile one pattern file's text (see `load_scan_patterns`)."""
    # Any failure below becomes one GuardPolicyError naming the source, so a bad file stops the
    # composition root at start rather than surfacing at the first tool result.
    try:
        document = ScanPatterns.model_validate({"families": tomllib.loads(text)})
    except (tomllib.TOMLDecodeError, ValidationError) as exc:
        raise GuardPolicyError(
            f"The untrusted-content patterns in {source} are invalid: {exc}"
        ) from exc
    families = tuple(
        CompiledFamily(
            name=name,
            weight=family.weight,
            max_hits=family.max_hits,
            detector=_detector_for(family),
            examples=family.examples,
        )
        for name, family in document.families.items()
    )
    return CompiledPatterns(families=families, source=source)


def _detector_for(family: PatternFamily | ProximityFamily | RunFamily | HostFamily) -> Detector:
    """Build the detector a family's `kind` names, with its patterns compiled."""
    if isinstance(family, PatternFamily):
        return PatternDetector(patterns=_compiled(family.patterns))
    if isinstance(family, ProximityFamily):
        return ProximityDetector(
            anchors=_compiled(family.anchors),
            verbs=_compiled(family.verbs),
            window_chars=family.window_chars,
        )
    if isinstance(family, RunFamily):
        return RunDetector(min_chars=family.min_chars, min_distinct=family.min_distinct)
    return HostDetector()


def _compiled(patterns: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    """Compile already-checked patterns with the scanner's flags."""
    return tuple(re.compile(pattern, _FLAGS) for pattern in patterns)
