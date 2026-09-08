"""Fail if a model id or provider URL literal appears outside `manifest/` and `docs/`.

Codingrules section 8.6 ("Model slots, not model names") requires that moving the Hive from
Claude to a locally hosted model be a configuration change, not a code change: every model id and
provider base URL must live in the Hive Manifest (`manifest/`), never hard-coded. This script is
the CI grep that rule promises. It scans Python source for a small set of model-id prefixes and
provider-URL fragments, but skips comments and docstrings first -- otherwise the OpenAI-compatible
adapter's own docstring, which has to *name* llama.cpp and Ollama to explain what it talks to,
would fail its own hygiene check.

Fits into the Hive:
    Layer: none (a dev-time gate, not shipped code). Enforces codingrules section 8.6's "a model
    id or provider URL in code is a lint failure" rule. Run by pre-commit and CI.

Key invariants:
    - Comments (tokenize COMMENT tokens) and docstrings (the first statement of a module, class,
      or function, per `ast`) are never scanned; every other string literal and every other piece
      of code is.
    - This file never flags itself: it is excluded from its own scan by path (see
      `_iter_candidate_files`), not by disguising the pattern literals below, because a plain
      exclusion is something a reader can verify by eye in one line, where a disguised literal
      would need re-deriving to check it says what it claims.

See Also:
    - .claude/codingrules.md section 8.6 for the rule this enforces.
    - hivemind.llm.providers, where a model id or provider URL is expected to appear (once that
      package has adapters; codingrules 8.6 confines vendor detail to exactly that package).
"""

from __future__ import annotations

import argparse
import ast
import io
import os
import tokenize
from collections.abc import Iterator, Sequence
from pathlib import Path

# Model-id prefixes, matched case-insensitively: any of these starting a run of id-like characters
# is almost certainly a hard-coded model name that belongs in the manifest instead.
MODEL_ID_PREFIXES = ("claude-", "gpt-", "llama")

# Provider-URL fragments: distinctive enough that a false positive (this exact substring appearing
# for an unrelated reason) is not realistically going to happen in this codebase.
PROVIDER_URL_FRAGMENTS = (
    "api.anthropic.com",
    "api.openai.com",
    "/v1/chat/completions",
    ":11434",  # Ollama's default port.
)

# Every pattern this script looks for, lower-cased once so the scan itself can be a plain
# case-folded substring check instead of re-lower-casing on every line.
_ALL_PATTERNS = tuple(p.lower() for p in (*MODEL_ID_PREFIXES, *PROVIDER_URL_FRAGMENTS))

# Directories never worth descending into (see scripts/check_sizes.py for the same rationale).
SKIP_DIR_NAMES = frozenset({"node_modules", ".venv", "dist", ".git"})

# This script's own path, resolved once, so `_iter_candidate_files` can exclude it (see the
# "Key invariants" note above on why path-exclusion was chosen over disguising the literals).
_SELF_PATH = Path(__file__).resolve()

__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    """Scan for model id and provider URL literals and print any findings.

    Args:
        argv: Command-line arguments, excluding the program name. ``None`` means use
            ``sys.argv[1:]``.

    Returns:
        0 if nothing was found, 1 otherwise.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Fail if a model id (claude-, gpt-, llama) or provider URL literal appears outside "
            "manifest/ and docs/, skipping comments and docstrings."
        )
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=["."],
        help="Files or directories to check (default: the whole repository).",
    )
    args = parser.parse_args(argv)

    findings: list[str] = []
    for path in sorted(_iter_candidate_files(args.paths)):
        findings.extend(_check_file(path))
    for finding in findings:
        print(finding)  # scripts/ is one of the two places codingrules section 12 allows print().
    return 1 if findings else 0


def _iter_candidate_files(paths: Sequence[str]) -> Iterator[Path]:
    """Yield every in-scope `.py` file: packages/*/src, packages/*/tests, and scripts/.

    Args:
        paths: Files or directories given on the command line.
    """
    for raw in paths:
        root = Path(raw)
        if root.is_file():
            if _is_in_scope(root):
                yield root
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
            for filename in filenames:
                candidate = Path(dirpath) / filename
                if candidate.suffix == ".py" and _is_in_scope(candidate):
                    yield candidate


def _is_in_scope(path: Path) -> bool:
    """Apply the scope from the module docstring: packages/*/{src,tests} and scripts/.

    Args:
        path: A candidate `.py` file.

    Returns:
        True if `path` should be scanned: it is under `packages/<pkg>/src/` or
        `packages/<pkg>/tests/`, or under `scripts/`; and it is not under a `manifest/`
        directory segment, not under `docs/`, and is not this script itself.
    """
    if path.resolve() == _SELF_PATH:
        return False  # See the module docstring's "Key invariants".
    parts = path.as_posix().split("/")
    if "manifest" in parts or "docs" in parts:
        return False  # codingrules 8.6: model ids/URLs are expected in the manifest and its docs.
    if parts and parts[0] == "scripts":
        return True
    return "packages" in parts and ("src" in parts or "tests" in parts)


def _check_file(path: Path) -> list[str]:
    """Scan one file's non-comment, non-docstring text for a model id or provider URL literal.

    Args:
        path: The file to scan.

    Returns:
        One finding string per matching line, or a single parse-failure finding if the file could
        not be tokenized.
    """
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
        masked_lines = _mask_comments_and_docstrings(source, tree)
    except (SyntaxError, tokenize.TokenError, IndentationError) as exc:
        return [f"{path}:1: could not scan for model ids ({exc})"]

    findings: list[str] = []
    for line_number, line in enumerate(masked_lines, start=1):
        lowered = line.lower()
        for pattern in _ALL_PATTERNS:
            if pattern in lowered:
                findings.append(
                    f"{path}:{line_number}: literal matching '{pattern}' outside "
                    "manifest/ and docs/ (codingrules 8.6)"
                )
    return findings


def _mask_comments_and_docstrings(source: str, tree: ast.Module) -> list[str]:
    """Return `source`'s lines with every comment and docstring blanked out (newlines kept).

    Blanking (rather than deleting) preserves line numbers, so a finding on the returned lines
    still points at the right place in the original file.

    Args:
        source: The file's full text.
        tree: `source` already parsed, used to locate docstring literals.

    Returns:
        `source` split into lines, with comment and docstring characters replaced by spaces.
    """
    docstring_starts = _docstring_start_positions(tree)
    chars = list(source)
    line_starts = _line_start_offsets(source)
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    for token in tokens:
        is_comment = token.type == tokenize.COMMENT
        is_docstring = token.type == tokenize.STRING and token.start in docstring_starts
        if is_comment or is_docstring:
            _blank_span(chars, line_starts, token.start, token.end)
    return "".join(chars).splitlines()


def _docstring_start_positions(tree: ast.Module) -> set[tuple[int, int]]:
    """Collect the (line, column) start of every module/class/function docstring literal."""
    positions: set[tuple[int, int]] = set()
    scopes: list[ast.AST] = [tree]
    scopes.extend(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
    )
    for scope in scopes:
        literal = _leading_string_literal(scope)
        if literal is not None:
            positions.add((literal.lineno, literal.col_offset))
    return positions


def _leading_string_literal(scope: ast.AST) -> ast.Constant | None:
    """Return `scope`'s first statement's string constant, if that first statement is one."""
    body = getattr(scope, "body", [])
    first = body[0] if body else None
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        return first.value
    return None


def _line_start_offsets(source: str) -> list[int]:
    """Return the absolute character offset where each 1-based line of `source` begins."""
    offsets = [0]
    for line in source.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def _blank_span(
    chars: list[str],
    line_starts: list[int],
    start: tuple[int, int],
    end: tuple[int, int],
) -> None:
    """Overwrite `chars` from `start` to `end` (tokenize's (row, col) positions) with spaces.

    Args:
        chars: The full source, as a mutable list of one-character strings.
        line_starts: `_line_start_offsets`'s result, mapping a 1-based row to an absolute offset.
        start: The token's start (row, col), 1-based row and 0-based column, per `tokenize`.
        end: The token's end (row, col), same convention.
    """
    start_offset = line_starts[start[0] - 1] + start[1]
    end_offset = line_starts[end[0] - 1] + end[1]
    for offset in range(start_offset, end_offset):
        if chars[offset] != "\n":  # Keep newlines so line numbers in the caller stay correct.
            chars[offset] = " "


if __name__ == "__main__":
    raise SystemExit(main())
