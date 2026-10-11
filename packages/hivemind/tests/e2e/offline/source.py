"""Fingerprint the shipped source: a checksum of every `src/` file, and git's view of it.

Phase 8's third exit criterion is "switching any slot between providers changes only the manifest;
a checksum of `src/` is identical between the two configurations in the e2e job". Read literally,
that is a claim about files on disk: run the same goal under two manifests, and the bytes of every
`packages/*/src/**/*.py` are the same before the first run, between the two and after the second.
This module computes that checksum (a sha256 over each file's path and bytes, in sorted order, so a
renamed, added or removed file changes it as surely as an edited one) and reads `git status
--porcelain` for the same paths, which also names an untracked file a run left behind (the checksum
sees only `.py` files), so the two together bound what a run can have written into the source tree.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by
    `tests/e2e/offline/test_offline_src_unchanged.py`. Calls the standard library, and `git` through
    `subprocess` (test infrastructure, not `hivemind` src: CLAUDE.md's subprocess rule governs src).

Key invariants:
    - The checksum covers `.py` files only and never a `__pycache__` directory, which the
      interpreter writes by itself.
    - `porcelain` returns None when git or the repository is absent, so a source archive run
      still checks the checksum.

See Also:
    - docs/adr/0045-provider-routing-forage-aware-fallbacks-and-offline-mode.md for the criterion.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]

__all__ = ["REPO_ROOT", "porcelain", "src_checksum"]


def src_checksum(root: Path = REPO_ROOT) -> str:
    """Return the sha256 hex digest over every `packages/*/src/**/*.py` under `root`."""
    digest = hashlib.sha256()
    for path in sorted(root.glob("packages/*/src/**/*.py")):
        if "__pycache__" in path.parts:
            continue
        # The path first, so moving a file's bytes to another name changes the digest too.
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def porcelain(root: Path = REPO_ROOT) -> str | None:
    """Return `git status --porcelain` for every package's `src/`, or None without git.

    An empty string is a clean source tree; anything else lists what differs from the index.
    """
    git = shutil.which("git")
    if git is None or not (root / ".git").exists():
        return None
    # SAFETY: a fixed argv with no shell and no user input; a read-only query of the repository.
    result = subprocess.run(  # noqa: S603 -- fixed argv, read-only git query.
        [git, "status", "--porcelain", "--", *sorted(str(p) for p in root.glob("packages/*/src"))],
        capture_output=True,
        text=True,
        check=True,
        cwd=root,
    )
    return result.stdout
