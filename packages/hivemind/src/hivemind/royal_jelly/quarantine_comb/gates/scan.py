"""Scan one tool source file for banned imports, calls and names: the static gate's AST walk.

The static gate reads source it never runs. This module is that reading: it parses a file with
`ast` and walks every node, resolving how names were imported (`import os as o` makes `o.system`
the same finding as `os.system`) and reporting each construct the banned lists forbid, plus every
import that is neither the standard library nor the session types. It is deliberately a walk of
the syntax tree and nothing cleverer: it cannot follow data flow, which is why the runner's audit
hook watches the same classes of action at run time. It reports every finding, with file and line,
so a model asked to fix the tool sees all of them at once.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.quarantine_comb.
    Called by `gates.static` once per source file. Calls into `gates.banned` and the standard
    library; never executes or imports the source it reads.

Key invariants:
    - Pure: bytes of source in, findings out; no I/O, no import of the scanned code.
    - A file that does not parse is one finding, never an exception.
    - Findings are ordered by line, then by discovery order, so output is stable.

See Also:
    - docs/adr/0047 (the Comb's gates and sandbox) for what the static gate guards.
    - hivemind.royal_jelly.quarantine_comb.gates.banned for the lists this walk applies.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass

from hivemind.royal_jelly.quarantine_comb.gates.banned import (
    ALLOWED_PREFIXES,
    BANNED_ANY_ATTRIBUTES,
    BANNED_BUILTINS,
    BANNED_NAMES,
    PATHLIB_IO_ATTRIBUTES,
    is_banned_module,
    is_banned_name,
)

OWN_MODULES = frozenset(
    {"tool", "test_tool"}
)  # The package's own two modules may import each other.

__all__ = ["OWN_MODULES", "Finding", "scan_source"]


@dataclass(frozen=True, slots=True)
class Finding:
    """One construct the scan refuses, with where it is."""

    file: str
    line: int
    message: str

    def text(self) -> str:
        """Render as `file:line: message`, the form a reason or detail line uses."""
        return f"{self.file}:{self.line}: {self.message}"


def scan_source(filename: str, source: str) -> tuple[Finding, ...]:
    """Parse `source` and return every banned construct in it.

    Args:
        filename: The file's name inside the package, for the findings.
        source: The file's text. Untrusted; it is parsed, never run.

    Returns:
        Findings in line order; a syntax error is a single finding.
    """
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as error:
        return (Finding(filename, error.lineno or 1, f"syntax error: {error.msg}"),)
    scanner = _Scanner(filename)
    scanner.visit(tree)
    return tuple(sorted(scanner.findings, key=lambda found: found.line))


def module_is_allowed(dotted: str) -> bool:
    """Return whether a tool may import `dotted`, ignoring the banned lists.

    Args:
        dotted: A module path as written in an import statement.

    Returns:
        True for the standard library, `hivemind.cell` and below, and the package's own modules.
    """
    top = dotted.split(".")[0]
    # A leading underscore marks an implementation module (`_socket`, `_posixsubprocess`) that
    # offers the banned capability under a name the lists above do not know.
    if top.startswith("_") and top != "__future__":
        return False
    if top in OWN_MODULES or top in sys.stdlib_module_names:
        return True
    return any(dotted == prefix or dotted.startswith(prefix + ".") for prefix in ALLOWED_PREFIXES)


class _Scanner(ast.NodeVisitor):
    """Walk one module, remembering what each imported name stands for."""

    def __init__(self, filename: str) -> None:
        """Start a walk of the file `filename` with no imports seen yet."""
        self.findings: list[Finding] = []
        self._filename = filename
        self._targets: dict[str, str] = {}  # Local name to the dotted thing it was imported as.

    def _add(self, node: ast.AST, message: str) -> None:
        """Record a finding at `node`'s line."""
        self.findings.append(Finding(self._filename, getattr(node, "lineno", 1), message))

    def visit_Import(self, node: ast.Import) -> None:  # ast's visitor naming.
        """Check each `import x [as y]`."""
        for alias in node.names:
            self._check_module(node, alias.name)
            # `import a.b` binds `a`; `import a.b as c` binds `c` to `a.b`.
            self._targets[alias.asname or alias.name.split(".")[0]] = (
                alias.name if alias.asname else alias.name.split(".")[0]
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # ast's naming.
        """Check `from m import n [as k]`, including the names taken from `m`."""
        module = node.module or ""
        if node.level:
            self._add(node, "uses a relative import; a tool is two flat modules")
            return
        # `from hivemind import cell` names a submodule, so only the submodule is judged.
        if module != "hivemind":
            self._check_module(node, module)
        for alias in node.names:
            if alias.name == "*":
                if module in BANNED_NAMES:
                    self._add(node, f"star-imports from '{module}', which hides banned names")
                continue
            if is_banned_name(module, alias.name):
                self._add(node, f"imports banned name '{module}.{alias.name}'")
            if module == "hivemind":
                self._check_module(node, f"hivemind.{alias.name}")
            self._targets[alias.asname or alias.name] = f"{module}.{alias.name}"

    def visit_Name(self, node: ast.Name) -> None:  # ast's visitor naming.
        """Flag a bare use of a banned builtin (`open`, `eval`, `__import__`, ...)."""
        if isinstance(node.ctx, ast.Load) and node.id in BANNED_BUILTINS:
            self._add(node, f"uses the banned builtin '{node.id}'")

    def visit_Attribute(self, node: ast.Attribute) -> None:  # ast's naming.
        """Flag `module.name` for a banned name, and any attribute banned on every object."""
        dotted = self._dotted(node.value)
        if node.attr in BANNED_ANY_ATTRIBUTES:
            self._add(node, f"uses the banned attribute '{node.attr}'")
        elif dotted is not None and is_banned_name(dotted, node.attr):
            self._add(node, f"uses banned '{dotted}.{node.attr}'")
        elif node.attr in PATHLIB_IO_ATTRIBUTES and self._imports_pathlib():
            self._add(node, f"uses pathlib file access '.{node.attr}'; use the session instead")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # ast's visitor naming.
        """Flag `getattr` with a computed or banned name, which hides an attribute from the scan."""
        if isinstance(node.func, ast.Name) and node.func.id == "getattr" and len(node.args) >= 2:
            name = node.args[1]
            if not (isinstance(name, ast.Constant) and isinstance(name.value, str)):
                self._add(node, "uses getattr with a computed name, which hides banned attributes")
            elif name.value in BANNED_ANY_ATTRIBUTES or any(
                name.value in names for names in BANNED_NAMES.values()
            ):
                self._add(node, f"uses getattr to reach the banned name '{name.value}'")
        self.generic_visit(node)

    def _imports_pathlib(self) -> bool:
        """Return whether any import so far reaches pathlib (`import pathlib` or `from pathlib`)."""
        # `from pathlib import Path` binds `Path` to `pathlib.Path`, so a prefix match is needed.
        return any(
            target == "pathlib" or target.startswith("pathlib.")
            for target in self._targets.values()
        )

    def _check_module(self, node: ast.AST, dotted: str) -> None:
        """Refuse a banned module, and any module that is neither stdlib nor an allowed one."""
        if is_banned_module(dotted):
            self._add(node, f"imports banned module '{dotted}'")
        elif not module_is_allowed(dotted):
            self._add(
                node,
                f"imports '{dotted}', which is neither the standard library nor hivemind.cell "
                "(the sandbox cannot install dependencies)",
            )

    def _dotted(self, node: ast.expr) -> str | None:
        """Resolve a `a.b.c` expression to what its root name was imported as, if it is a chain."""
        parts: list[str] = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if not isinstance(node, ast.Name):
            return None
        root = self._targets.get(node.id, node.id)
        return ".".join([root, *reversed(parts)])
