"""Walk the source tree: nothing but the one setter and the one clearer ever writes a taint label.

Roadmap step 10.6d (ADR-0035): "Set by isolation (10.6a), by quarantine (10.6c), or by the Queen
on a Guard report about a Honey item, and by nothing else; a test asserts no other path writes it."
This is that test. It parses every module under `hivemind/` and fails when a label is written
anywhere but the two functions allowed to: a `write_taint(...)` call or a `TaintMarker(...)` built
outside `memory/taint/set.py` and `memory/taint/clear.py`; a `"tainted"` key set outside the stores
that persist the label; a `tainted=` argument that is not a label read off another item; or a call
to `taint_memory` from a module that is not one of the three setters. The next steps add their own
module to `_SETTER_CALLERS` (10.6a: `queen/isolation.py`; 10.6c: the quarantine path in
`wardens/`), each bound to its own `TaintSource`.

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
# The three setters (ADR-0035), by module, each with the only TaintSource it may pass. None is
# built yet: 10.6a and 10.6c land the first two; the Queen's Guard-report path lands with phase 7.
_SETTER_CALLERS: dict[str, str] = {
    "hivemind/queen/isolation.py": "ISOLATION",
    "hivemind/wardens/quarantine": "QUARANTINE",
    "hivemind/queen/guard_reports": "GUARD_REPORT",
}


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
