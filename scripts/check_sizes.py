"""Fail if a Python, TypeScript or TSX file breaks the codingrules 5.1 size limits.

The Hive's whole design bet on readability (codingrules section 1: "small pieces, sharp edges")
only holds if the limits are actually enforced, not just documented. This script walks the given
paths (or the whole repository) and checks every `.py`, `.ts` and `.tsx` file against four limits:
file length, function/method length, class length, and parameter count. Python is checked exactly,
via the standard library's `ast` module; TypeScript and TSX have no such parser available without
adding a dependency, so their function-length check is a documented heuristic delegated to
`scripts/size_rules_ts.py` (codingrules 5.2: one concept per file).

Fits into the Hive:
    Layer: none (a dev-time gate, not shipped code). Enforces codingrules section 5.1's size
    limits. Run by pre-commit and CI (see .pre-commit-config.yaml) and by a developer directly.

Key invariants:
    - Exits 0 with no output when every file is within limits, 1 with one finding per offending
      line otherwise. Never raises for a file it cannot parse; a parse failure is itself reported
      as a finding rather than crashing the whole run (one bad file should not hide every other
      finding).

See Also:
    - .claude/codingrules.md section 5.1 for the limits enforced here.
    - scripts/size_rules_ts.py for the TypeScript/TSX function-length heuristic.
"""

from __future__ import annotations

import argparse
import ast
import os
from collections.abc import Iterator, Sequence
from pathlib import Path

from size_rules_ts import find_function_length_violations

# codingrules 5.1: target/hard limits. We enforce the hard limits; the tighter targets are a
# review nudge a human applies, not something a script can judge (is 210 lines "close enough"?).
FILE_LINE_LIMIT = 300
TEST_FILE_LINE_LIMIT = 400  # codingrules 5.1: "Test files may go to 400 lines."
FUNCTION_LINE_LIMIT = 50
CLASS_LINE_LIMIT = 200
PARAM_COUNT_LIMIT = 5  # codingrules 5.1: excludes self/cls, see _count_parameters.

# Directories never worth descending into: a virtualenv or node_modules can hold tens of thousands
# of files that are not ours to lint, and walking into them would just be slow.
SKIP_DIR_NAMES = frozenset({"node_modules", ".venv", "dist", ".git"})

# codingrules section 3, rule 3: generated code is exempt from the rules that govern hand-written
# code. This file is regenerated from docs/entrance/openapi.json by CI and never hand-edited.
GENERATED_EXEMPT_SUFFIX = "packages/observation-web/src/landing_board/types.ts"

_PY_SUFFIX = ".py"
_TS_SUFFIXES = frozenset({".ts", ".tsx"})

__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    """Run the size checks over `argv`'s paths (or the whole repo) and print any findings.

    Args:
        argv: Command-line arguments, excluding the program name. ``None`` means use
            ``sys.argv[1:]`` (the normal case when run as a script).

    Returns:
        0 if no file exceeded a limit, 1 otherwise -- the shell/CI exit-code convention.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Check Python/TypeScript/TSX files against the codingrules 5.1 size limits: "
            "file length, function/method length, class length, and parameter count."
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
    for path in sorted(_iter_target_files(args.paths)):
        source = path.read_text(encoding="utf-8")
        if path.suffix == _PY_SUFFIX:
            findings.extend(_check_python_source(path, source))
        else:
            findings.extend(_check_typescript_source(path, source))

    for finding in findings:
        print(finding)  # scripts/ is one of the two places codingrules section 12 allows print().
    return 1 if findings else 0


def _iter_target_files(paths: Sequence[str]) -> Iterator[Path]:
    """Yield every `.py`/`.ts`/`.tsx` file under `paths`, skipping generated and vendored trees.

    Args:
        paths: Files or directories given on the command line.
    """
    suffixes = {_PY_SUFFIX, *_TS_SUFFIXES}
    for raw in paths:
        root = Path(raw)
        if root.is_file():
            if root.suffix in suffixes:
                yield root
            continue
        # os.walk (not Path.rglob) so SKIP_DIR_NAMES can prune traversal in place instead of
        # filtering results after the fact -- rglob would still have to walk into .venv first.
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
            for filename in filenames:
                candidate = Path(dirpath) / filename
                if candidate.suffix in suffixes and not _is_generated_exempt(candidate):
                    yield candidate


def _is_generated_exempt(path: Path) -> bool:
    """Return True for the one generated TS file exempt from these rules (see module docstring)."""
    return path.as_posix().endswith(GENERATED_EXEMPT_SUFFIX)


def _check_python_source(path: Path, source: str) -> list[str]:
    """Check one Python file's text against all four codingrules 5.1 limits.

    Args:
        path: The file's path, used only to label findings.
        source: The file's full text.

    Returns:
        One finding string per violation, or a single parse-failure finding if `source` is not
        valid Python.
    """
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        # A file that does not parse cannot be size-checked further; report that fact and move on
        # rather than letting the exception abort every other file's check.
        return [f"{path}:{exc.lineno or 1}: could not parse as Python ({exc.msg})"]

    limit = TEST_FILE_LINE_LIMIT if "tests" in path.parts else FILE_LINE_LIMIT
    findings = _check_line_count(path, source, limit)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            findings.extend(_check_span_length(path, node, "class", CLASS_LINE_LIMIT))
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            findings.extend(_check_span_length(path, node, "function", FUNCTION_LINE_LIMIT))
            findings.extend(_check_param_count(path, node))
    return findings


def _check_typescript_source(path: Path, source: str) -> list[str]:
    """Check one TS/TSX file's text: exact file length plus the heuristic function-length check.

    Args:
        path: The file's path, used only to label findings.
        source: The file's full text.
    """
    limit = TEST_FILE_LINE_LIMIT if "tests" in path.parts else FILE_LINE_LIMIT
    findings = _check_line_count(path, source, limit)
    findings.extend(find_function_length_violations(source, str(path)))
    return findings


def _check_line_count(path: Path, source: str, limit: int) -> list[str]:
    """Flag a file whose line count exceeds `limit`. Shared by the Python and TS/TSX checks."""
    line_count = len(source.splitlines())
    if line_count > limit:
        return [f"{path}:1: file is {line_count} lines (limit {limit})"]
    return []


def _check_span_length(
    path: Path,
    node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
    kind: str,
    limit: int,
) -> list[str]:
    """Flag a class or function/method whose body, including its `def`/`class` line, is too long."""
    end = node.end_lineno if node.end_lineno is not None else node.lineno
    length = end - node.lineno + 1
    if length > limit:
        return [f"{path}:{node.lineno}: {kind} '{node.name}' is {length} lines (limit {limit})"]
    return []


def _check_param_count(path: Path, node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Flag a function/method with more than PARAM_COUNT_LIMIT parameters, excluding self/cls."""
    args = node.args
    names = [a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)]
    # self/cls document the binding the method is called on, not a value a caller chooses --
    # codingrules 5.1 counts parameters, so they are excluded regardless of position.
    count = sum(1 for name in names if name not in ("self", "cls"))
    if count > PARAM_COUNT_LIMIT:
        return [
            f"{path}:{node.lineno}: function '{node.name}' has {count} parameters "
            f"(limit {PARAM_COUNT_LIMIT})"
        ]
    return []


if __name__ == "__main__":
    raise SystemExit(main())
