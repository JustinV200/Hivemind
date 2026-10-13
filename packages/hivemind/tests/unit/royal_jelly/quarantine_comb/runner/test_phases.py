"""Test the runner's phases in process: tool loading, unittest run, schema check and fuzzing.

Fits into the Hive:
    Mirrors src/hivemind/royal_jelly/quarantine_comb/runner/phases.py
    (codingrules section 3). Test infrastructure, not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - src/hivemind/royal_jelly/quarantine_comb/runner/phases.py for the module under test.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from hivemind.cell.fake import FakeSession
from hivemind.royal_jelly.quarantine_comb.runner.phases import (
    ToolLoadError,
    check_schema,
    load_tool,
    run_fuzz,
    run_unittests,
)
from hivemind.royal_jelly.quarantine_comb.runner.session import RecordingSession
from hivemind.royal_jelly.quarantine_comb.runner.usage import CountingWriter, DeadlineExceededError
from waggle.clock import FakeClock

_CLOSED = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "additionalProperties": False,
}


def _session() -> RecordingSession:
    """Build a recording session over an in-memory fake."""
    return RecordingSession(FakeSession(Path("/scratch"), FakeClock()))


def _package(tmp_path: Path, tool: str, tests: str) -> Path:
    """Write a two-file package and return its directory."""
    (tmp_path / "tool.py").write_text(tool, encoding="utf-8")
    (tmp_path / "test_tool.py").write_text(tests, encoding="utf-8")
    return tmp_path


@pytest.fixture(autouse=True)
def _restore_sys_path() -> object:
    """Put sys.path and sys.modules back after a test that imports a package."""
    saved_path, saved_modules = list(sys.path), set(sys.modules)
    yield
    sys.path[:] = saved_path
    for name in set(sys.modules) - saved_modules:
        if name.startswith(("comb_", "tool", "test_tool")):
            del sys.modules[name]


# ── loading ─────────────────────────────────────────────────────────────────────


def test_a_tool_loads_under_a_unique_module_name(tmp_path: Path) -> None:
    package = _package(tmp_path, "VALUE = 5\n", "")

    first, second = load_tool(package), load_tool(package)

    assert first.VALUE == 5
    assert first.__name__ != second.__name__


def test_a_tool_that_raises_at_import_is_a_load_error_naming_the_exception(tmp_path: Path) -> None:
    package = _package(tmp_path, "raise RuntimeError('boom')\n", "")

    with pytest.raises(ToolLoadError, match="RuntimeError: boom"):
        load_tool(package)


# ── unittest run ────────────────────────────────────────────────────────────────

_PASSING = "import unittest\n\nclass T(unittest.TestCase):\n    def test_a(self):\n        pass\n"


def test_passing_tests_are_counted(tmp_path: Path) -> None:
    section = run_unittests(_package(tmp_path, "", _PASSING), _session(), CountingWriter())

    assert section.ran == 1
    assert section.failures == ()
    assert section.error == ""


def test_failing_and_erroring_tests_get_one_line_each(tmp_path: Path) -> None:
    tests = (
        "import unittest\n\nclass T(unittest.TestCase):\n"
        "    def test_fail(self):\n        self.assertEqual(1, 2)\n"
        "    def test_error(self):\n        raise KeyError('k')\n"
        "    def test_ok(self):\n        pass\n"
    )

    section = run_unittests(_package(tmp_path, "", tests), _session(), CountingWriter())

    assert section.ran == 3
    assert len(section.failures) == 2
    assert any("test_fail" in line and "AssertionError" in line for line in section.failures)
    assert any("test_error" in line and "KeyError" in line for line in section.failures)


def test_a_test_module_that_does_not_import_is_an_error_not_a_crash(tmp_path: Path) -> None:
    section = run_unittests(
        _package(tmp_path, "", "import nope_missing\n"), _session(), CountingWriter()
    )

    assert section.ran == 0
    assert "nope_missing" in section.error


def test_the_test_module_receives_the_session_even_over_a_placeholder(tmp_path: Path) -> None:
    tests = (
        "import unittest\nSESSION = None\n\nclass T(unittest.TestCase):\n"
        "    def test_has_session(self):\n"
        "        self.assertIsNotNone(SESSION)\n"
        "        self.assertTrue(SESSION.is_open)\n"
    )

    section = run_unittests(_package(tmp_path, "", tests), _session(), CountingWriter())

    assert section.failures == ()
    assert section.ran == 1


def test_a_test_can_import_the_tool_by_name(tmp_path: Path) -> None:
    tests = (
        "import unittest\nfrom tool import VALUE\n\nclass T(unittest.TestCase):\n"
        "    def test_value(self):\n        self.assertEqual(VALUE, 3)\n"
    )

    section = run_unittests(_package(tmp_path, "VALUE = 3\n", tests), _session(), CountingWriter())

    assert (section.ran, section.failures) == (1, ())


def test_what_the_tests_print_is_counted_not_forwarded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tests = (
        "import unittest\n\nclass T(unittest.TestCase):\n"
        "    def test_print(self):\n        print('x' * 100)\n"
    )
    out = CountingWriter()

    run_unittests(_package(tmp_path, "", tests), _session(), out)

    assert out.bytes_written >= 101
    assert "xxxx" not in capsys.readouterr().out


# ── schema check ────────────────────────────────────────────────────────────────


def test_a_closed_schema_compiles_accepts_its_sample_and_refuses_an_extra() -> None:
    section = check_schema(json.dumps(_CLOSED))

    assert (section.compiled, section.sample_accepted, section.extra_refused) == (True, True, True)


def test_an_open_schema_accepts_an_extra_property() -> None:
    schema = {"type": "object", "properties": {"text": {"type": "string"}}}

    section = check_schema(json.dumps(schema))

    assert section.compiled
    assert not section.extra_refused


@pytest.mark.parametrize("text", ["{not json", "[]", '{"type": "string"}'])
def test_a_schema_that_does_not_compile_says_why(text: str) -> None:
    section = check_schema(text)

    assert not section.compiled
    assert section.findings


def test_a_default_of_the_wrong_type_makes_the_sample_refused() -> None:
    schema = {**_CLOSED, "properties": {"n": {"type": "integer", "default": "x"}}}

    section = check_schema(json.dumps(schema))

    assert section.compiled
    assert not section.sample_accepted
    assert section.findings


# ── fuzzing ─────────────────────────────────────────────────────────────────────

_INPUTS = [{"text": "a"}, {"text": ""}, {}]


async def test_json_results_pass() -> None:
    async def run(session: object, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"echo": arguments}

    section = await run_fuzz(run, _session(), _INPUTS, 1.0)  # type: ignore[arg-type]

    assert (section.cases, section.failure) == (3, "")


async def test_ordinary_exceptions_are_clean_raises_and_are_counted() -> None:
    async def run(session: object, arguments: dict[str, Any]) -> dict[str, Any]:
        raise ValueError("bad text")

    section = await run_fuzz(run, _session(), _INPUTS, 1.0)  # type: ignore[arg-type]

    assert section.failure == ""
    assert section.clean_raises == {"ValueError": 3}


async def test_a_call_past_its_timeout_fails_the_run_at_that_case() -> None:
    async def run(session: object, arguments: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(30)
        return {}

    section = await run_fuzz(run, _session(), _INPUTS, 0.05)  # type: ignore[arg-type]

    assert section.cases == 1
    assert "case 0: timed out" in section.failure


async def test_a_tools_own_timeout_error_is_a_clean_raise_not_our_timeout() -> None:
    async def run(session: object, arguments: dict[str, Any]) -> dict[str, Any]:
        raise TimeoutError("upstream")

    section = await run_fuzz(run, _session(), _INPUTS, 1.0)  # type: ignore[arg-type]

    assert section.failure == ""
    assert section.clean_raises == {"TimeoutError": 3}


async def test_a_result_that_is_not_json_fails() -> None:
    async def run(session: object, arguments: dict[str, Any]) -> object:
        return object()

    section = await run_fuzz(run, _session(), _INPUTS, 1.0)  # type: ignore[arg-type]

    assert "not JSON-serialisable" in section.failure


async def test_a_base_exception_fails() -> None:
    class Boom(BaseException):
        pass

    async def run(session: object, arguments: dict[str, Any]) -> dict[str, Any]:
        raise Boom

    section = await run_fuzz(run, _session(), _INPUTS, 1.0)  # type: ignore[arg-type]

    assert "Boom" in section.failure


async def test_a_synchronous_run_function_fails_without_being_called() -> None:
    def run(session: object, arguments: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("must not be called")

    section = await run_fuzz(run, _session(), _INPUTS, 1.0)  # type: ignore[arg-type]

    assert "async def" in section.failure


async def test_the_deadline_always_propagates() -> None:
    async def run(session: object, arguments: dict[str, Any]) -> dict[str, Any]:
        raise DeadlineExceededError

    with pytest.raises(DeadlineExceededError):
        await run_fuzz(run, _session(), _INPUTS, 1.0)  # type: ignore[arg-type]


async def test_a_tool_that_mutates_its_arguments_does_not_change_later_cases() -> None:
    seen: list[dict[str, Any]] = []

    async def run(session: object, arguments: dict[str, Any]) -> dict[str, Any]:
        seen.append(dict(arguments))
        arguments["poison"] = True
        return {}

    inputs = [{"text": "a"}]
    await run_fuzz(run, _session(), inputs, 1.0)  # type: ignore[arg-type]

    assert inputs == [{"text": "a"}]
