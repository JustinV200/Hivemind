"""Estimate TypeScript/TSX function length and lines of code with text heuristics.

`scripts/check_sizes.py` (Layer: none, a dev-time gate) needs the same "function <= 50 lines" and
"file <= 300 lines of code" rules from codingrules section 5.1 applied to `.ts`/`.tsx` files under
`packages/observation-web/`, but Python's `ast` module cannot parse TypeScript. Adding a real
TypeScript parser would pull a third-party dependency into a hygiene script that otherwise needs
none, so this module trades precision for zero dependencies: it recognises common
function-declaration shapes with a regular expression, then counts braces to find where the body
ends. This is a heuristic, not a parser --
see "Known gaps" below for what it misses. Kept as a sibling of `check_sizes.py` rather than
folded into it, per codingrules 5.2 (one concept per file) and to keep `check_sizes.py` itself
under the file-length limit it enforces on everyone else.

Fits into the Hive:
    Layer: none (a dev-time gate, not shipped code). Called only by `scripts/check_sizes.py`.

Key invariants:
    - Never raises on malformed or unusual input: a declaration whose braces never balance (for
      example because a string literal on the line confused the counter) is silently skipped
      rather than reported, so the heuristic's own gaps never crash the checker.

Known gaps (documented instead of fixed, because fixing them means writing a TS parser):
    - Only matches a declaration whose opening `{` is on the same source line as its signature.
      A function whose parameter list wraps onto its own line before the `{` is not recognised.
    - Counts brace characters in the raw text. A `{` or `}` inside a string, template literal, or
      regular expression is counted like any other brace, which can over- or under-count a
      function's length or, in the worst case, make its braces look unbalanced (in which case it
      is skipped -- see the invariant above -- rather than mis-reported).
    - Arrow functions are only recognised when assigned directly to a `const`/`let`/`var`
      (`const f = (x) => { ... }`). An arrow function passed inline as a callback argument
      (`arr.map((x) => { ... })`) has no declaration line to anchor on and is not counted.
    - A one-line arrow function with an expression body and no braces (`const f = (x) => x + 1`)
      is never long enough to matter and is intentionally not matched.
    - `count_code_lines` strips `//` lines and `/* ... */` blocks by text. A comment marker inside
      a string or template literal is taken for a real one, so such a line (and, for an unpaired
      `/*`, the lines after it) may be left uncounted; the error is always in the file's favour.

See Also:
    - .claude/codingrules.md section 5.1 for the limits this heuristic approximates.
    - scripts/check_sizes.py for the caller and the file-length half of the same rule.
"""

from __future__ import annotations

import re

# codingrules 5.1: function/method hard limit. Same number for TS as for Python -- section 3 says
# "Sections 5 and 7 apply to .ts/.tsx unchanged."
TS_FUNCTION_LINE_LIMIT = 50

# Matches a `function` declaration or a class method, ending in the body's opening brace on the
# same line: optional export/visibility/async modifiers, a name, a parameter list, an optional
# return-type annotation, then `{`. The parameter list is `[^)]*` (anything but `)`) rather than a
# real grammar, which is why a `{` inside a parameter's object-type annotation is not itself
# miscounted: the regex consumes straight through to its own trailing `{`, so brace counting (see
# `_scan_for_matching_brace`) only ever starts at the real body brace, never at one inside `(...)`.
_FUNCTION_DECL_RE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?"
    r"(?:public\s+|private\s+|protected\s+|static\s+|async\s+)*"
    r"(?:function\s+)?([A-Za-z_$][\w$]*)\s*\(([^)]*)\)\s*(?::\s*[^{=]+)?\s*\{"
)

# Matches `const/let/var name = (...) => {`, the other common declaration shape prettier produces.
_ARROW_DECL_RE = re.compile(
    r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*"
    r"(?:async\s*)?\(([^)]*)\)\s*(?::\s*[^{=]+)?=>\s*\{"
)

__all__ = ["count_code_lines", "find_function_length_violations"]


def count_code_lines(source: str) -> int:
    r"""Count the lines of a TS/TSX file that carry code: not blank, not only a comment.

    Args:
        source: The file's full text.

    Returns:
        The number of lines with something other than whitespace, a `//` comment or the inside
        of a `/* ... */` block on them. A line that mixes code and a comment counts once.

    Example:
        >>> count_code_lines("// header\nconst x = 1; // why\n/* a\n block */\n\n")
        1
    """
    count = 0
    in_block = False
    for raw in source.splitlines():
        line, in_block = _strip_block_comments(raw.strip(), in_block)
        if line and not line.startswith("//"):
            count += 1
    return count


def _strip_block_comments(line: str, in_block: bool) -> tuple[str, bool]:
    """Remove every `/* ... */` span from `line`, carrying an open block across lines.

    Args:
        line: One source line, already stripped of surrounding whitespace.
        in_block: Whether the previous line ended inside an unterminated block comment.

    Returns:
        What remains of the line outside comments, stripped, and whether a block is still open.
    """
    # Inside a block from an earlier line: everything up to its close is comment.
    if in_block:
        end = line.find("*/")
        if end == -1:
            return "", True
        line = line[end + 2 :].strip()
    # Then remove each complete block on this line; an opener with no close leaves the block open.
    while (start := line.find("/*")) != -1:
        end = line.find("*/", start + 2)
        if end == -1:
            return line[:start].strip(), True
        line = (line[:start] + line[end + 2 :]).strip()
    return line, False


def find_function_length_violations(source: str, display_path: str) -> list[str]:
    r"""Report every TS/TSX function whose heuristic length exceeds the limit.

    Args:
        source: The file's full text.
        display_path: How to name the file in a finding line (the caller's `Path`, stringified).

    Returns:
        One `"path:line: message"` string per over-length function found. Empty if the file is
        clean, or if every declaration in it fell into one of the documented heuristic gaps.

    Example:
        >>> find_function_length_violations("function f() {\\n}\\n", "a.ts")
        []
    """
    lines = source.splitlines()
    findings: list[str] = []
    # Every line is a candidate declaration start; most lines match neither regex and are skipped
    # in one comparison each, so this stays linear in file size despite the per-line regex probes.
    for index, line in enumerate(lines):
        match = _FUNCTION_DECL_RE.match(line) or _ARROW_DECL_RE.match(line)
        if match is None:
            continue
        name = match.group(1)
        start_line = index + 1
        end_line = _scan_for_matching_brace(lines, index, match.end())
        if end_line is None:
            continue  # Unbalanced braces: a heuristic gap (see module docstring), not a finding.
        length = end_line - start_line + 1
        if length > TS_FUNCTION_LINE_LIMIT:
            findings.append(
                f"{display_path}:{start_line}: function '{name}' is {length} lines "
                f"(limit {TS_FUNCTION_LINE_LIMIT})"
            )
    return findings


def _scan_for_matching_brace(lines: list[str], start_index: int, start_col: int) -> int | None:
    """Find the 1-based line where the brace opened at `start_col` closes.

    The regex match already consumed exactly one opening `{`, so the running depth starts at 1
    and we scan forward, character by character, for the `}` that brings it back to 0.

    Args:
        lines: The file, already split into lines (no trailing newlines).
        start_index: 0-based index into `lines` of the declaration's own line.
        start_col: Column just past the opening `{` on that line, where scanning resumes.

    Returns:
        The 1-based line number where the body's closing brace was found, or ``None`` if the
        braces never balance before the file ends (see the module's "Known gaps").
    """
    depth = 1
    column = start_col
    # Scan remaining lines one at a time; each iteration looks only at the slice from `column`
    # onward, so re-visiting characters already scanned on a previous line never happens.
    for line_index in range(start_index, len(lines)):
        segment = lines[line_index][column:]
        for character in segment:
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    return line_index + 1
        column = 0  # Every line after the first is scanned from its own start.
    return None
