"""Prove waggle imports only the standard library, pydantic, websockets and cryptography.

Spec section 1 and codingrules section 4 make waggle its own layer: hivemind and pollen both
import it and it imports neither, and its only third-party dependencies are pydantic,
websockets and cryptography, so a Pollen Packet (the thin gateway on an enrolled device) can
speak the protocol with nothing else installed. import-linter polices the hivemind and pollen
direction; this module is the phase 1 exit criterion for the dependency set, scanning every
module under src/waggle with ``ast`` so an import hidden in a function body or under
TYPE_CHECKING counts too. It is the check the dependency test bullet of spec section 11 names.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Reads the waggle source tree relative to this
    file; imports nothing from it and nothing depends on it.

Key invariants:
    - None: this module holds tests only.

See Also:
    - docs/waggle/spec.md section 1 (the dependency sentence) and section 11 (this test).
    - packages/waggle/pyproject.toml for the three dependencies declared.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

# packages/waggle/tests/test_dependencies.py -> tests -> packages/waggle -> src/waggle.
SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "waggle"
# The three dependencies packages/waggle/pyproject.toml declares, plus the package itself.
ALLOWED_PACKAGES = frozenset({"pydantic", "websockets", "cryptography", "waggle"})
# The two workspace packages that depend on waggle; an import of either would be a cycle.
FORBIDDEN_PACKAGES = frozenset({"hivemind", "pollen"})

MODULES = tuple(sorted(SRC_ROOT.rglob("*.py")))


def test_the_source_tree_was_found() -> None:
    assert MODULES, f"No modules under {SRC_ROOT}"


@pytest.mark.parametrize("path", MODULES, ids=lambda path: path.relative_to(SRC_ROOT).as_posix())
def test_module_imports_only_the_standard_library_and_the_declared_packages(path: Path) -> None:
    unexpected = sorted(
        name
        for name in _top_level_imports(path)
        if name not in sys.stdlib_module_names and name not in ALLOWED_PACKAGES
    )

    assert not unexpected, f"{path.name} imports packages waggle may not depend on: {unexpected}"


def test_no_module_imports_hivemind_or_pollen() -> None:
    offenders = {
        path.relative_to(SRC_ROOT).as_posix(): sorted(_top_level_imports(path) & FORBIDDEN_PACKAGES)
        for path in MODULES
        if _top_level_imports(path) & FORBIDDEN_PACKAGES
    }

    assert not offenders, f"waggle modules importing a package that depends on waggle: {offenders}"


def _top_level_imports(path: Path) -> set[str]:
    """Return the top-level package name of every import statement in ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    # ast.walk visits imports at every depth: a module body, a function, a TYPE_CHECKING block.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # A relative import (level > 0) stays inside waggle by construction; an absolute
            # one names its package in the first dotted segment.
            names.add("waggle" if node.level else (node.module or "").partition(".")[0])
    return names
