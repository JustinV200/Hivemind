"""Walk the source tree: nothing but the one setter and the one clearer ever writes a taint label.

Roadmap step 10.6d (ADR-0043): "Set by isolation (10.6a), by quarantine (10.6c), or by the Queen
on a Guard report about a Honey item, and by nothing else; a test asserts no other path writes it."
This is that test. It parses every module under `hivemind/` and fails when a label is written
anywhere but the two functions allowed to: a `write_taint(...)` call or a `TaintMarker(...)` built
outside `memory/taint/set.py` and `memory/taint/clear.py`; a `"tainted"` key set outside the stores
that persist the label; a `tainted=` argument that is not a label read off another item; or a call
to `taint_memory` from a module that is not one of the three setters. Each setter's module is in
`_SETTER_CALLERS`, bound to its own `TaintSource` (10.6a: the isolation package in `queen/`, and its
in-Cell setter call, the one module `wardens/isolation/taint.py`; 10.6c: the quarantine path in
`wardens/`). Both have landed, so each is also pinned as real and single: exactly one module in the
whole tree calls `taint_memory` with `TaintSource.QUARANTINE`, the one quarantine path, and exactly
two with `TaintSource.ISOLATION`: the isolation path's taint step on the Hive's tables, and the
in-Cell setter call a Warden makes on its own store at the Queen's order
(`wardens/isolation/taint.py`, roadmap step 10.6a: a Virtual Cell's store lives inside the Cell,
where her label cannot reach). Nothing else in `wardens/` or in either isolation package calls the
setter at all.

Fits into the Hive:
    Mirrors src/hivemind/memory/taint/set.py's key invariant (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.memory.taint.set for taint_memory, and .clear for clear_taint.
"""

from __future__ import annotations

import ast
import functools
from pathlib import Path

import pytest

import hivemind

_SRC = Path(hivemind.__file__).resolve().parent.parent  # packages/hivemind/src
_WRITERS = ("hivemind/memory/taint/set.py", "hivemind/memory/taint/clear.py")
_LABEL_STORES = ("hivemind/memory/store/",)  # The stores that persist a label they were handed.
# Roadmap 10.6a's in-Cell half: the Warden runs the setter on its own store at the Queen's order.
_IN_CELL_ISOLATION_TAINT = "hivemind/wardens/isolation/taint.py"
# The three setters (ADR-0043), by module, each with the only TaintSource it may pass. 10.6c's is
# built; 10.6a lands the first, and the Queen's Guard-report path lands with phase 7.
_SETTER_CALLERS: dict[str, str] = {
    "hivemind/queen/isolation/": "ISOLATION",
    _IN_CELL_ISOLATION_TAINT: "ISOLATION",  # That one module, not its package.
    "hivemind/wardens/quarantine": "QUARANTINE",
    "hivemind/queen/guard_reports": "GUARD_REPORT",
}
_QUARANTINE_PATH = "hivemind/wardens/quarantine/path.py"  # Roadmap 10.6c's one code path.
_ISOLATION_TAINT = "hivemind/queen/isolation/taint.py"  # Roadmap 10.6a's one taint step.


@functools.cache
def _modules() -> tuple[tuple[str, ast.Module], ...]:
    """Every source module under hivemind/, as (posix path relative to src, parsed tree).

    Parsed once per test session: the walk is the same for every test below.
    """
    modules = []
    for path in sorted(_SRC.joinpath("hivemind").rglob("*.py")):
        relative = path.relative_to(_SRC).as_posix()
        modules.append((relative, ast.parse(path.read_text(encoding="utf-8"), filename=relative)))
    return tuple(modules)


def _called_name(call: ast.Call) -> str | None:
    """The bare name a call invokes: `f(...)` or `x.f(...)` both give "f"."""
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _calls(tree: ast.Module, name: str) -> list[ast.Call]:
    return [
        node for node in ast.walk(tree) if isinstance(node, ast.Call) and _called_name(node) == name
    ]


def test_the_walk_sees_the_writers_themselves() -> None:
    # Guards the test against walking the wrong tree and passing vacuously.
    found = dict(_modules())

    assert _calls(found["hivemind/memory/taint/set.py"], "write_taint")
    assert _calls(found["hivemind/memory/taint/clear.py"], "TaintMarker")


@pytest.mark.parametrize("name", ["write_taint", "TaintMarker"])
def test_only_the_setter_and_the_clearer_write_a_label(name: str) -> None:
    offenders = [path for path, tree in _modules() if _calls(tree, name) and path not in _WRITERS]

    assert offenders == []


def test_a_tainted_argument_only_ever_carries_a_label_read_off_another_item() -> None:
    offenders = []
    for path, tree in _modules():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                propagated = (
                    isinstance(keyword.value, ast.Attribute) and keyword.value.attr == "tainted"
                )
                if keyword.arg == "tainted" and not propagated and path not in _WRITERS:
                    offenders.append(f"{path}:{node.lineno}")

    assert offenders == []


def test_only_the_label_stores_set_a_tainted_key() -> None:
    offenders = [
        f"{path}:{node.lineno}"
        for path, tree in _modules()
        for node in ast.walk(tree)
        if isinstance(node, ast.Dict)
        and any(isinstance(key, ast.Constant) and key.value == "tainted" for key in node.keys)
        and not path.startswith(_LABEL_STORES)
    ]

    assert offenders == []


def test_only_the_three_setters_call_taint_memory_each_with_its_own_source() -> None:
    callers = [path for path, tree in _modules() if _calls(tree, "taint_memory")]

    for path in callers:
        allowed = [prefix for prefix in _SETTER_CALLERS if path.startswith(prefix)]
        assert allowed, f"{path} calls taint_memory but is not one of the three setters"
        source = _SRC.joinpath(path).read_text(encoding="utf-8")
        assert f"TaintSource.{_SETTER_CALLERS[allowed[0]]}" in source


def _names_source(tree: ast.Module, member: str) -> bool:
    """Whether a module's code (not its prose) names `TaintSource.<member>`."""
    return any(
        isinstance(node, ast.Attribute)
        and node.attr == member
        and isinstance(node.value, ast.Name)
        and node.value.id == "TaintSource"
        for node in ast.walk(tree)
    )


def test_the_one_quarantine_path_is_the_only_quarantine_setter() -> None:
    callers = [path for path, tree in _modules() if _calls(tree, "taint_memory")]
    quarantining = [path for path, tree in _modules() if _names_source(tree, "QUARANTINE")]

    # Real, not vacuous: the path calls the setter, and names the source, and nothing else does.
    assert quarantining == [_QUARANTINE_PATH]
    # In wardens/, only the quarantine path and the in-Cell isolation setter call it.
    wardens_callers = [path for path in callers if path.startswith("hivemind/wardens/")]
    assert sorted(wardens_callers) == sorted([_QUARANTINE_PATH, _IN_CELL_ISOLATION_TAINT])


def test_the_two_isolation_taint_steps_are_the_only_isolation_setters() -> None:
    callers = [path for path, tree in _modules() if _calls(tree, "taint_memory")]
    isolating = [path for path, tree in _modules() if _names_source(tree, "ISOLATION")]
    # Real, not vacuous: each step calls the setter and names the source, and nothing else does:
    # the Queen's on the Hive's tables, and the Warden's on the store it keeps inside its Cell.
    assert sorted(isolating) == sorted([_ISOLATION_TAINT, _IN_CELL_ISOLATION_TAINT])
    assert [path for path in callers if path.startswith("hivemind/queen/isolation/")] == [
        _ISOLATION_TAINT
    ]
    assert [path for path in callers if path.startswith("hivemind/wardens/isolation/")] == [
        _IN_CELL_ISOLATION_TAINT
    ]
