"""Fail if a `messages`/`history` name accumulates state outside `hivemind/memory/`.

Codingrules section 8.8 says an awake episode (one turn where a Warden -- a per-Cell supervisor --
or the Queen consults a model) is stateless: its prompt is assembled fresh each time by
`memory.assemble` from durable state, and the transcript is discarded afterwards. "Continuity
lives in hot state or a pending question, never in a transcript." A module-level or instance
attribute named `messages` or `history` that grows call after call is exactly the anti-pattern that
rule forbids: it would silently reintroduce a conversation the Hive is supposed to keep no memory
of between episodes. This script flags that shape via `ast`: an assignment, annotation, or
`.append(`/`.extend(`/`+=` growth on a name spelled `messages` or `history`, wherever it is not
supposed to live.

Fits into the Hive:
    Layer: none (a dev-time gate, not shipped code). Enforces codingrules section 8.8's
    "awake episodes are stateless" rule. Run by pre-commit and CI.

Key invariants:
    - `hivemind/memory/` is exempt entirely: accumulating state across episodes is that
      subsystem's job.
    - Three narrow allowlist entries (and their mirrored test files) exist because the roadmap
      step defining this checker named them explicitly: `hivemind/llm/models.py`, where
      `LLMRequest.messages` is one outgoing request, not a growing transcript; `hivemind/llm/
      tools.py`, where a tool-call loop legitimately accumulates messages within a single episode
      by design; and `waggle/messages/`, which is a *package* name (codingrules 6.2: plural
      directory names hold a collection of that thing, here Waggle message types), not an
      instance attribute -- coincidentally sharing the word this checker looks for, not an
      example of the pattern it exists to catch.
    - Only a module-level or class-level bare name, or an attribute access (`x.messages`), is
      flagged. A local variable inside a function body that happens to be named `messages` (for
      example one built and returned within a single call) is not module/class state and is left
      alone -- see `_check_module_and_class_level_names`.

See Also:
    - .claude/codingrules.md section 8.8 for the rule this enforces.
    - hivemind.memory, the one subsystem allowed to hold a growing transcript.
"""

from __future__ import annotations

import argparse
import ast
import os
from collections.abc import Iterator, Sequence
from pathlib import Path

# The two names codingrules 8.8 warns about: a message list or a history log that outlives one
# awake episode.
TARGET_NAMES = frozenset({"messages", "history"})

SKIP_DIR_NAMES = frozenset({"node_modules", ".venv", "dist", ".git"})

__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    """Scan for accumulating `messages`/`history` names outside the allowlist and print findings.

    Args:
        argv: Command-line arguments, excluding the program name. ``None`` means use
            ``sys.argv[1:]``.

    Returns:
        0 if nothing was found, 1 otherwise.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Fail if a module-level/class/instance name called 'messages' or 'history' is "
            "assigned, annotated, or grown outside hivemind/memory/ (codingrules 8.8)."
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
    """Yield every `.py` file not exempt by the allowlist in the module docstring.

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
    """Apply the allowlist from the module docstring to one file."""
    parts = path.parts
    if "memory" in parts:
        return False  # The one subsystem where a growing transcript is the whole point.
    if "messages" in parts and "waggle" in parts:
        return False  # waggle/messages/ is a package name, not an attribute (see docstring).
    stem = path.name.removeprefix("test_")
    is_allowlisted_llm_file = stem in ("models.py", "tools.py") and "llm" in parts
    # The two explicitly-named exceptions, plus their mirrored test files.
    return not is_allowlisted_llm_file


def _check_file(path: Path) -> list[str]:
    """Find every disallowed accumulating `messages`/`history` name in one file.

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

    findings = _check_module_and_class_level_names(path, tree)
    findings.extend(_check_attribute_growth(path, tree))
    return findings


def _check_module_and_class_level_names(path: Path, tree: ast.Module) -> list[str]:
    """Flag a bare `messages`/`history` name assigned, annotated, or grown at module/class scope.

    Deliberately restricted to each scope's own direct body (not `ast.walk`, which would also
    reach into nested function bodies): a local variable shadowing one of these names inside a
    function is not module or class state, and codingrules 8.8 only forbids the latter.
    """
    findings: list[str] = []
    scopes: list[list[ast.stmt]] = [tree.body]
    scopes.extend(node.body for node in ast.walk(tree) if isinstance(node, ast.ClassDef))
    for body in scopes:
        for stmt in body:
            findings.extend(_check_name_statement(path, stmt))
    return findings


def _check_name_statement(path: Path, stmt: ast.stmt) -> list[str]:
    """Check one module/class-body statement for a bare target name in TARGET_NAMES."""
    if isinstance(stmt, ast.Assign):
        names = [t.id for t in stmt.targets if isinstance(t, ast.Name) and t.id in TARGET_NAMES]
    elif isinstance(stmt, ast.AnnAssign | ast.AugAssign) and isinstance(stmt.target, ast.Name):
        names = [stmt.target.id] if stmt.target.id in TARGET_NAMES else []
    else:
        names = []
    return [
        f"{path}:{stmt.lineno}: module/class-level '{name}' looks like an accumulating "
        "transcript; transcripts belong in hivemind.memory"
        for name in names
    ]


def _check_attribute_growth(path: Path, tree: ast.Module) -> list[str]:
    """Flag an attribute (`obj.messages`/`obj.history`) that is assigned, annotated, or grown.

    Unlike a bare name, an attribute access is never "just a local variable": whatever object it
    is set on owns that state for as long as the object lives, which is the instance-attribute
    shape codingrules 8.8 forbids regardless of how deep in the file it is written.
    """
    findings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign | ast.AugAssign):
            targets: list[ast.expr] = (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
            findings.extend(_flag_attribute_targets(path, node.lineno, targets))
        elif isinstance(node, ast.AnnAssign):
            findings.extend(_flag_attribute_targets(path, node.lineno, [node.target]))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("append", "extend"):
                findings.extend(_flag_growth_call(path, node.lineno, node.func))
    return findings


def _flag_attribute_targets(path: Path, lineno: int, targets: list[ast.expr]) -> list[str]:
    """Flag any Attribute target in `targets` whose attribute name is in TARGET_NAMES."""
    return [
        f"{path}:{lineno}: attribute '.{t.attr}' looks like an accumulating transcript; "
        "transcripts belong in hivemind.memory"
        for t in targets
        if isinstance(t, ast.Attribute) and t.attr in TARGET_NAMES
    ]


def _flag_growth_call(path: Path, lineno: int, func: ast.Attribute) -> list[str]:
    """Flag `x.messages.append(...)`/`messages.extend(...)`-shaped calls.

    Args:
        path: The file being checked, for the finding's label.
        lineno: The call's source line.
        func: The call's `.append`/`.extend` attribute access; `func.value` is the receiver.
    """
    receiver = func.value
    name = None
    if isinstance(receiver, ast.Name) and receiver.id in TARGET_NAMES:
        name = receiver.id
    elif isinstance(receiver, ast.Attribute) and receiver.attr in TARGET_NAMES:
        name = receiver.attr
    if name is None:
        return []
    return [
        f"{path}:{lineno}: '{name}.{func.attr}(...)' grows a transcript; "
        "transcripts belong in hivemind.memory"
    ]


if __name__ == "__main__":
    raise SystemExit(main())
