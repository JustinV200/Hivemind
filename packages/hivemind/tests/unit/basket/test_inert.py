"""Tests for the rule "a blob is inert": holding one grants nothing, and nothing here runs one.

This is the rule's first half (ADR-0048): the Basket's public surface has no way to execute,
install, load or import a blob, and the package imports nothing that could. The second half, that
the Comb tool runner refuses a hash with no registry entry, arrives with that runner (wave 2E).
The check is by reflection and by reading the source's syntax tree, so a method added later with
such a name, or a stray `subprocess` import, fails here rather than in review.

Fits into the Hive:
    Mirrors the package-level rule across src/hivemind/basket (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - docs/adr/0048-basket-inert-blobs-residency-and-transfer.md for the rule.
    - .claude/codingrules.md section 15, "Nothing is executed from the Honey Store".
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

import hivemind.basket as basket
from hivemind.basket import (
    BasketDeposits,
    BasketStore,
    ResidentBasket,
    SqliteBasketStore,
    TransferReceiver,
    TransferSender,
)

_FORBIDDEN_FRAGMENTS = ("exec", "run", "install", "load", "import")
_BASKET_ROOT = Path(basket.__file__).parent
# `importlib.resources` reads the migration .sql files as data and never loads code; it is the one
# importlib use allowed, in the one module that applies migrations.
_ALLOWED_IMPORTLIB = {("store/migrate.py", "importlib.resources")}
_FORBIDDEN_MODULES = {"subprocess", "importlib", "runpy", "pty", "ctypes", "marshal", "pickle"}
_FORBIDDEN_CALLS = {"exec", "eval", "compile", "__import__"}


def _public_names(cls: type) -> list[str]:
    return [name for name, _ in inspect.getmembers(cls) if not name.startswith("_")]


@pytest.mark.parametrize(
    "cls",
    [
        BasketStore,
        SqliteBasketStore,
        BasketDeposits,
        ResidentBasket,
        TransferSender,
        TransferReceiver,
    ],
)
def test_no_public_method_name_suggests_running_installing_or_loading_a_blob(cls: type) -> None:
    offenders = [
        name
        for name in _public_names(cls)
        if any(bad in name.lower() for bad in _FORBIDDEN_FRAGMENTS)
    ]

    assert offenders == []


def test_the_public_face_names_nothing_that_runs_a_blob() -> None:
    offenders = [
        name for name in basket.__all__ if any(bad in name.lower() for bad in _FORBIDDEN_FRAGMENTS)
    ]

    assert offenders == []


def _imports_and_calls() -> list[tuple[str, str, str]]:
    """Return (relative path, kind, name) for every import and builtin call in the package."""
    found: list[tuple[str, str, str]] = []
    for path in sorted(_BASKET_ROOT.rglob("*.py")):
        relative = path.relative_to(_BASKET_ROOT).as_posix()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                found += [(relative, "import", alias.name) for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                found.append((relative, "import", node.module))
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in _FORBIDDEN_CALLS
            ):
                found.append((relative, "call", node.func.id))
    return found


def test_the_package_imports_nothing_that_can_execute_or_load_code() -> None:
    offenders = [
        (path, name)
        for path, kind, name in _imports_and_calls()
        if kind == "import"
        and name.split(".")[0] in _FORBIDDEN_MODULES
        and (path, name) not in _ALLOWED_IMPORTLIB
    ]

    assert offenders == []


def test_the_package_calls_no_exec_eval_compile_or_dunder_import() -> None:
    offenders = [(path, name) for path, kind, name in _imports_and_calls() if kind == "call"]

    assert offenders == []


def test_the_one_allowed_importlib_use_exists_and_is_the_resource_reader() -> None:
    uses = {
        (path, name)
        for path, kind, name in _imports_and_calls()
        if kind == "import" and name.split(".")[0] == "importlib"
    }

    assert uses == _ALLOWED_IMPORTLIB


def test_the_package_is_not_allowed_to_reach_the_stores_or_layers_above_it() -> None:
    forbidden = (
        "hivemind.honey_store",
        "hivemind.memory",
        "hivemind.supervision",
        "hivemind.llm",
        "hivemind.brood_chamber",
        "hivemind.queen",
        "hivemind.wardens",
        "hivemind.workers",
    )
    offenders = [
        (path, name)
        for path, kind, name in _imports_and_calls()
        if kind == "import" and name.startswith(forbidden)
    ]

    assert offenders == []
