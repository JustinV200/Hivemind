"""Test the source scan: every import spelling, banned attribute and name it must catch.

Fits into the Hive:
    Mirrors src/hivemind/royal_jelly/quarantine_comb/gates/scan.py
    (codingrules section 3). Test infrastructure, not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - src/hivemind/royal_jelly/quarantine_comb/gates/scan.py for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.royal_jelly.quarantine_comb.gates.banned import (
    BANNED_MODULES,
    BANNED_NAMES,
    is_banned_module,
    is_banned_name,
)
from hivemind.royal_jelly.quarantine_comb.gates.scan import module_is_allowed, scan_source


def _messages(source: str) -> list[str]:
    """Scan `source` and return the finding messages."""
    return [finding.message for finding in scan_source("tool.py", source)]


@pytest.mark.parametrize(
    ("source", "needle"),
    [
        ("import socket", "socket"),
        ("import socket as s", "socket"),
        ("from socket import socket", "socket"),
        ("from subprocess import run", "subprocess"),
        ("import asyncio.subprocess", "asyncio.subprocess"),
        ("from asyncio import create_subprocess_exec", "create_subprocess_exec"),
        ("from asyncio import subprocess", "asyncio.subprocess"),
        ("import urllib.request", "urllib.request"),
        ("from http.client import HTTPConnection", "http.client"),
        ("import os\nos.system('x')", "os.system"),
        ("import os as o\no.popen('x')", "os.popen"),
        ("import os\nos.execvp('x', [])", "os.execvp"),
        ("import os\nos.spawnl(0, 'x')", "os.spawnl"),
        ("from os import fork", "os.fork"),
        ("from os import *", "star-imports"),
        ("import os\nos.remove('x')", "os.remove"),
        ("import os\nos.rename('x', 'y')", "os.rename"),
        ("import os\nos.replace('x', 'y')", "os.replace"),
        ("from os import link", "os.link"),
        ("import os\nos.symlink('x', 'y')", "os.symlink"),
        ("from pathlib import Path\nPath('x').rename('y')", "'.rename'"),
        ("from pathlib import Path\nPath('x').replace('y')", "'.replace'"),
        ("import pathlib\npathlib.Path('x').symlink_to('y')", "'.symlink_to'"),
        ("import pathlib\npathlib.Path('x').hardlink_to('y')", "'.hardlink_to'"),
        ("import sys\nsys.modules['x']", "sys.modules"),
        ("import sys\nsys.settrace(None)", "sys.settrace"),
        ("import builtins\nbuiltins.open('x')", "builtins.open"),
        ("import io\nio.open('x')", "io.open"),
        ("open('x')", "'open'"),
        ("eval('1')", "'eval'"),
        ("exec('1')", "'exec'"),
        ("compile('1', 'x', 'eval')", "'compile'"),
        ("__import__('os')", "'__import__'"),
        ("breakpoint()", "'breakpoint'"),
        ("import importlib", "importlib"),
        ("import _socket", "_socket"),
        ("import numpy", "numpy"),
        ("import hivemind.llm", "hivemind.llm"),
        ("from hivemind import llm", "hivemind.llm"),
        ("from . import sibling", "relative import"),
        ("getattr(x, name)", "computed name"),
        ("getattr(x, 'system')", "'system'"),
        ("loop.subprocess_exec(f)", "subprocess_exec"),
        ("().__class__.__subclasses__()", "__subclasses__"),
        ("import pathlib\npathlib.Path('x').read_text()", "read_text"),
    ],
)
def test_each_banned_construct_is_found(source: str, needle: str) -> None:
    assert any(needle in message for message in _messages(source)), _messages(source)


@pytest.mark.parametrize(
    "source",
    [
        "import json, re, asyncio, unittest, os.path, pathlib",
        "import os\nos.path.join('a', 'b')\nos.environ.get('x')",
        "from hivemind.cell.session import ExecSpec, run",
        "from hivemind import cell",
        "import hivemind.cell",
        "from tool import run",
        "import sys\nsys.stdout.write('x')",
        "import pathlib\nx = pathlib.Path('a') / 'b'\nx.name",
        "async def run(session, arguments):\n    return getattr(arguments, 'x')",
        "from __future__ import annotations",
        "import time, math, hashlib, base64, dataclasses, typing, collections, itertools",
    ],
)
def test_ordinary_tool_code_is_clean(source: str) -> None:
    # `getattr` with a constant harmless name is allowed; computed names are not (tested above).
    assert [m for m in _messages(source) if "getattr" not in m or "computed" in m] == []


def test_findings_carry_the_file_and_the_line() -> None:
    findings = scan_source("test_tool.py", "x = 1\nimport socket\n")

    assert findings[0].text().startswith("test_tool.py:2:")


def test_a_syntax_error_is_one_finding() -> None:
    assert len(scan_source("tool.py", "def (:")) == 1


def test_the_banned_lists_hold_what_the_design_names() -> None:
    for module in (
        "socket",
        "subprocess",
        "ctypes",
        "multiprocessing",
        "importlib",
        "http",
        "urllib",
    ):
        assert module in BANNED_MODULES
    assert {"system", "popen", "fork", "kill", "remove", "unlink", "rmdir"} <= BANNED_NAMES["os"]
    assert {"rename", "renames", "replace", "link", "symlink"} <= BANNED_NAMES["os"]
    assert {"modules", "settrace", "setprofile", "addaudithook"} == BANNED_NAMES["sys"]
    assert {"open", "eval", "exec", "compile", "__import__", "breakpoint"} == BANNED_NAMES[
        "builtins"
    ]


def test_banning_a_module_bans_everything_below_it() -> None:
    assert is_banned_module("urllib.parse")
    assert is_banned_module("asyncio.subprocess")
    assert not is_banned_module("asyncio")
    assert is_banned_name("os", "execvpe")
    assert not is_banned_name("os", "getcwd")


def test_module_allowance_is_stdlib_session_types_and_own_modules() -> None:
    assert module_is_allowed("json")
    assert module_is_allowed("hivemind.cell.session")
    assert module_is_allowed("tool")
    assert not module_is_allowed("hivemind.llm")
    assert not module_is_allowed("hivemind")
    assert not module_is_allowed("_thread")
