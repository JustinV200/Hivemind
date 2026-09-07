"""Fail if code branches on `cell.kind` instead of `cell.capabilities`.

Codingrules section 8.7 ("Branch on capabilities, never on kind") says `CellKind` -- the enum
telling a Cell (a terminal session on a real device or a virtual machine/container the Hive
manages) apart as REAL or VIRTUAL -- matters to exactly two callers: placement (choosing where a
task runs) and the Undertaker (the cleanup Worker deciding whether to release a borrowed device or
destroy a provisioned one). Everywhere else, code that special-cases a Cell by kind instead of by
what it can actually do (`cell.capabilities`) is a review rejection, because it silently breaks the
moment a new kind of Cell shows up that the branch never considered. This script is the mechanical
version of that review: it walks the AST for a comparison or `match` against `CellKind`, or against
a `.kind` attribute, outside the allowed callers.

Fits into the Hive:
    Layer: none (a dev-time gate, not shipped code). Enforces codingrules section 8.7's
    capabilities-not-kind rule. Run by pre-commit and CI.

Key invariants:
    - The allowlist is exactly three places, matching codingrules 8.7 and 6.1's "Placement" and
      "Worker roles" rows: `hivemind/queen/placement/` (the placement decision itself),
      `hivemind/workers/roles/undertaker.py` (release-vs-destroy), and `hivemind/cell/` (where
      `CellKind` is defined -- codingrules 6.1: "Cell (either kind) | cell | ... CellKind ...").
    - Test files are exempt everywhere: asserting a Cell's kind in a test is verifying behaviour,
      not branching on it in production code.

See Also:
    - .claude/codingrules.md section 8.7 for the rule this enforces.
    - .claude/codingrules.md section 6.1 for the CellKind/Placement/Undertaker table rows.
"""

from __future__ import annotations

import argparse
import ast
import os
from collections.abc import Iterator, Sequence
from pathlib import Path

# codingrules 8.7 names exactly these two callers, plus 6.1 names cell/ as where CellKind lives.
# Matched as a substring of the file's posix-style relative path, so both a package's __init__.py
# and every module under it are covered by one entry.
ALLOWED_PATH_FRAGMENTS = (
    "hivemind/queen/placement/",
    "hivemind/workers/roles/undertaker.py",
    "hivemind/cell/",
)

# Operators that express an equality-style branch. `in`/`not in` are deliberately excluded: a
# membership test against a collection of kinds is a different (and rarer) pattern this script
# does not attempt to characterise; codingrules 8.7 calls out `is`/`==` specifically.
_BRANCH_OPS = (ast.Eq, ast.NotEq, ast.Is, ast.IsNot)

SKIP_DIR_NAMES = frozenset({"node_modules", ".venv", "dist", ".git"})

__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    """Scan for `cell.kind`/`CellKind` branches outside the allowlist and print any findings.

    Args:
        argv: Command-line arguments, excluding the program name. ``None`` means use
            ``sys.argv[1:]``.

    Returns:
        0 if nothing was found, 1 otherwise.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Fail if code compares against CellKind or a .kind attribute outside "
            "hivemind/queen/placement/, hivemind/workers/roles/undertaker.py and hivemind/cell/."
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
    """Yield every `.py` file not exempt by allowlist or by being a test.

    Args:
        paths: Files or directories given on the command line.
    """
    for raw in paths:
        root = Path(raw)
        if root.is_file():
            if _should_scan(root):
                yield root
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
            for filename in filenames:
                candidate = Path(dirpath) / filename
                if candidate.suffix == ".py" and _should_scan(candidate):
                    yield candidate


def _should_scan(path: Path) -> bool:
    """Return False for a test file or one under an allowlisted path fragment."""
    if "tests" in path.parts:
        return False  # codingrules 8.7: asserting a kind in a test is legitimate.
    rel = path.as_posix()
    return not any(fragment in rel for fragment in ALLOWED_PATH_FRAGMENTS)


def _check_file(path: Path) -> list[str]:
    """Find every disallowed kind-branch in one file.

    Args:
        path: The file to scan.

    Returns:
        One finding string per violation, or a single parse-failure finding if it is not valid
        Python.
    """
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [f"{path}:{exc.lineno or 1}: could not parse as Python ({exc.msg})"]

    findings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            findings.extend(_check_compare(path, node))
        elif isinstance(node, ast.Match):
            findings.extend(_check_match(path, node))
    return findings


def _check_compare(path: Path, node: ast.Compare) -> list[str]:
    """Flag a Compare node if it tests equality/identity against a `CellKind.<MEMBER>` operand."""
    operands = [node.left, *node.comparators]
    if not any(isinstance(op, _BRANCH_OPS) for op in node.ops):
        return []  # Not an equality/identity comparison (e.g. `<`): out of scope for this rule.
    if any(_is_cellkind_member(operand) for operand in operands):
        return [
            f"{path}:{node.lineno}: branches on a CellKind member; branch on capabilities instead"
        ]
    return []


def _check_match(path: Path, node: ast.Match) -> list[str]:
    """Flag a `match` statement whose subject is a `.kind` attribute."""
    if isinstance(node.subject, ast.Attribute) and node.subject.attr == "kind":
        return [f"{path}:{node.lineno}: matches on .kind; branch on capabilities instead"]
    return []


def _is_cellkind_member(node: ast.expr) -> bool:
    """Return True if `node` is `CellKind.<MEMBER>` or `<something>.CellKind.<MEMBER>`."""
    if not isinstance(node, ast.Attribute):
        return False
    base = node.value
    if isinstance(base, ast.Name):
        return base.id == "CellKind"
    return isinstance(base, ast.Attribute) and base.attr == "CellKind"


if __name__ == "__main__":
    raise SystemExit(main())
