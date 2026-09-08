"""Fail if a source directory holds more modules than codingrules 5.6 allows.

A directory listing is the first map a reader gets of a subsystem, and codingrules 5.6 keeps it
readable: at most MODULE_LIMIT modules directly in one source directory, `__init__.py` excluded,
with a sub-package counting as one entry of its own and never against its parent. Past the limit
a concept that has outgrown one file becomes a package (codingrules 5.2), which is how the
message families and the outbox under waggle came to be packages. This script walks every
`packages/<name>/src` tree (Python and TypeScript alike) and reports each directory over the
limit. Test trees mirror src (codingrules 3) and may split one module's tests by feature (5.1),
so they are not counted; `scripts/` is flat dev tooling run by path, not a package, and is not
counted either.

Fits into the Hive:
    Layer: none (a dev-time gate, not shipped code). Enforces codingrules 5.6. Run by pre-commit
    and CI (see .pre-commit-config.yaml) and by a developer directly.

Key invariants:
    - Exits 0 with no output when every directory is within the limit, 1 with one finding per
      offending directory otherwise. A path that does not exist is a finding, not a crash, so
      a typo on the command line cannot pass as a clean run.

See Also:
    - .claude/codingrules.md sections 5.2 and 5.6 for the rule enforced here.
    - scripts/check_sizes.py for the per-file limits of the same section.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Iterator, Sequence
from pathlib import Path

MODULE_LIMIT = 10  # codingrules 5.6: the hard limit per directory; the target is six.
SOURCE_SUFFIXES = frozenset({".py", ".ts", ".tsx"})  # The languages codingrules section 5 governs.
EXCLUDED_NAMES = frozenset({"__init__.py"})  # The package face names the directory, not a module.
# Never worth descending into: vendored or generated trees are not ours, and a tests/ tree under
# src would mirror src rather than add modules of its own.
SKIP_DIR_NAMES = frozenset({"node_modules", ".venv", "dist", ".git", "__pycache__", "tests"})
DEFAULT_ROOTS = ("packages",)  # Every packages/<name>/src tree, found by _is_source_directory.

__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    """Run the fan-out check over `argv`'s paths (or every package) and print any findings.

    Args:
        argv: Command-line arguments, excluding the program name. ``None`` means use
            ``sys.argv[1:]`` (the normal case when run as a script).

    Returns:
        0 if no directory exceeded the limit, 1 otherwise -- the shell/CI exit-code convention.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Check every source directory under packages/*/src against the codingrules 5.6 "
            "limit on modules per directory."
        )
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=list(DEFAULT_ROOTS),
        help="Directories to walk (default: every packages/<name>/src tree).",
    )
    args = parser.parse_args(argv)

    findings = [finding for root in args.paths for finding in _check_tree(Path(root))]
    for finding in findings:
        print(finding)  # scripts/ is one of the two places codingrules section 12 allows print().
    return 1 if findings else 0


def _check_tree(root: Path) -> Iterator[str]:
    """Yield one finding per source directory under `root` that holds too many modules.

    Args:
        root: A directory to walk; a missing one yields a finding of its own.
    """
    if not root.is_dir():
        yield f"{root}:0: not a directory"
        return
    # os.walk (not Path.rglob) so SKIP_DIR_NAMES can prune traversal in place.
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in SKIP_DIR_NAMES)
        directory = Path(dirpath)
        if not _is_source_directory(directory):
            continue
        count = sum(1 for name in filenames if _is_module(name))
        if count > MODULE_LIMIT:
            yield (
                f"{directory}:0: directory holds {count} modules (limit {MODULE_LIMIT}); "
                "group them into sub-packages (codingrules 5.6)"
            )


def _is_source_directory(directory: Path) -> bool:
    """Return True when `directory` is at or below a `packages/<name>/src` tree."""
    # Walk up from the directory itself: the first ancestor named src whose grandparent is
    # packages marks a source tree, wherever the repository itself is checked out.
    for candidate in (directory, *directory.parents):
        if candidate.name == "src" and candidate.parent.parent.name == "packages":
            return True
    return False


def _is_module(filename: str) -> bool:
    """Return True for a Python, TS or TSX file that counts as a module of its directory."""
    return Path(filename).suffix in SOURCE_SUFFIXES and filename not in EXCLUDED_NAMES


if __name__ == "__main__":
    raise SystemExit(main())
