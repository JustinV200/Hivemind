"""Apply a unified diff to a file's prior bytes: new-file, whole-file and ordinary context hunks.

`apply_unified_diff` is the pure function behind a DIFF proposal's apply step (`hivemind.
supervision.capping.apply`): given the bytes a path held before (or `None` for a file that did not
exist) and a unified diff's text, it returns the bytes the path should hold after. It walks each
`@@ -old_start,old_len +new_start,new_len @@` hunk in order, copying unchanged lines, requiring
every context (" ") and removal ("-") line to match the prior content exactly, and inserting every
addition ("+") line -- a new-file hunk is the special case where `prior` is empty and every line is
an addition; a whole-file replacement hunk is the special case where every prior line is a removal.
A mismatch (a context or removal line that does not match) raises `DiffApplyError` rather than
silently producing a garbled file.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the supervision package. Called
    by `hivemind.supervision.capping.apply` for every path a DIFF proposal touches. Calls into
    `hivemind.supervision.capping.errors` only; no I/O of its own (codingrules section 8.3: "pure
    core, effectful edges" -- the session read/write lives in apply.py, not here).

Key invariants:
    - apply_unified_diff never mutates `prior`; it always returns a new `bytes` object.
    - A diff with no recognisable `@@` hunks raises DiffApplyError rather than returning `prior`
      unchanged, so a malformed or empty diff is never silently a no-op.
    - Every context and removal line must match the prior content at its exact cursor position;
      a mismatch raises DiffApplyError naming the line, never a best-effort patch.

See Also:
    - .claude/codingrules.md section 8.3 for "pure core, effectful edges," the shape this module
      and hivemind.supervision.capping.apply together follow.
    - hivemind.supervision.capping.apply for apply_action, this function's one caller.
    - hivemind.supervision.capping.errors for DiffApplyError.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from hivemind.supervision.capping.errors import DiffApplyError

# `@@ -old_start[,old_len] +new_start[,new_len] @@`: only old_start is read here (the walk tracks
# its own cursor from the hunk's content, so old_len/new_len are not needed to apply correctly).
_HUNK_HEADER_PATTERN = re.compile(r"^@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@")
# A unified diff's per-line prefixes: " " context, "-" removal, "+" addition.
_LINE_TAGS = frozenset({" ", "-", "+"})

__all__ = ["apply_unified_diff"]


@dataclass(frozen=True, slots=True)
class _Hunk:
    """One `@@ ... @@` block: where it starts in the prior file, and its tagged lines."""

    old_start: int  # 1-based line number in the prior file this hunk begins at; 0 for a new file.
    lines: tuple[str, ...]  # Each element is a tag character (" ", "-", "+") plus its text.


def apply_unified_diff(prior: bytes | None, diff_text: str, *, path: str = "<diff>") -> bytes:
    """Apply `diff_text` to `prior` and return the resulting bytes.

    Args:
        prior: The path's bytes before this diff, or None when the path did not exist (a new-file
            diff).
        diff_text: The unified diff to apply.
        path: The target path, folded into any DiffApplyError so a caller applying more than one
            path's diff in a loop can tell which one failed.

    Returns:
        The bytes the path should hold after applying every hunk in `diff_text`, in order.

    Raises:
        DiffApplyError: `diff_text` has no recognisable hunks, or a context or removal line does
            not match `prior` at the position the hunk expects it.
    """
    prior_lines = ("" if prior is None else prior.decode("utf-8")).splitlines()
    hunks = _parse_hunks(diff_text, path)
    result: list[str] = []
    cursor = 0  # Index into prior_lines already consumed.
    for hunk in hunks:
        # A hunk's old_start is 1-based, and 0 for a brand-new file (nothing precedes it); either
        # way this is the 0-based index in prior_lines the hunk's own lines start reconciling from.
        start = max(hunk.old_start - 1, 0)
        result.extend(prior_lines[cursor:start])
        cursor = start
        cursor = _apply_hunk_lines(hunk, prior_lines, result, cursor, path)
    result.extend(prior_lines[cursor:])
    return "\n".join(result).encode("utf-8")


def _apply_hunk_lines(
    hunk: _Hunk, prior_lines: list[str], result: list[str], cursor: int, path: str
) -> int:
    """Walk one hunk's tagged lines, appending to `result` and advancing past consumed context.

    Args:
        hunk: The hunk being applied.
        prior_lines: The full prior file, split into lines.
        result: The output accumulated so far; appended to in place.
        cursor: Index into prior_lines already consumed, before this hunk's lines.
        path: The target path, for a DiffApplyError's message.

    Returns:
        The cursor's new position after this hunk's context and removal lines are consumed.

    Raises:
        DiffApplyError: A context or removal line does not match prior_lines at `cursor`.
    """
    for line in hunk.lines:
        tag, text = line[0], line[1:]
        if tag == "+":
            result.append(text)
            continue
        # " " (context) and "-" (removal) both require the prior file to hold exactly this line
        # at the cursor; only their handling of the cursor and the output differs below.
        if cursor >= len(prior_lines) or prior_lines[cursor] != text:
            kind = "context" if tag == " " else "removal"
            raise DiffApplyError(path, f"{kind} mismatch at prior line {cursor + 1}")
        if tag == " ":
            result.append(text)
        cursor += 1
    return cursor


def _parse_hunks(diff_text: str, path: str) -> tuple[_Hunk, ...]:
    """Split `diff_text` into its `@@ ... @@` hunks.

    Args:
        diff_text: The unified diff to parse.
        path: The target path, for a DiffApplyError's message.

    Returns:
        Every hunk found, in the order they appear.

    Raises:
        DiffApplyError: `diff_text` contains no `@@ ... @@` header at all.
    """
    hunks: list[_Hunk] = []
    old_start: int | None = None
    lines: list[str] = []
    for raw_line in diff_text.splitlines():
        # `---`/`+++` file-header lines carry no hunk content; skip them wherever they appear.
        if raw_line.startswith("--- ") or raw_line.startswith("+++ "):
            continue
        header = _HUNK_HEADER_PATTERN.match(raw_line)
        if header:
            if old_start is not None:
                hunks.append(_Hunk(old_start=old_start, lines=tuple(lines)))
            old_start, lines = int(header.group(1)), []
            continue
        if old_start is None:
            continue  # Text before the first hunk header (a diff --git line) carries no content.
        # An untagged blank line in the diff text is a context line with empty content; every
        # other line already starts with one of " ", "-", "+".
        lines.append(raw_line if raw_line[:1] in _LINE_TAGS else f" {raw_line}")
    if old_start is not None:
        hunks.append(_Hunk(old_start=old_start, lines=tuple(lines)))
    if not hunks:
        raise DiffApplyError(path, "no hunks found in diff text")
    return tuple(hunks)
