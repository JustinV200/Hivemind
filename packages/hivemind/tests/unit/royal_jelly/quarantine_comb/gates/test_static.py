"""Test the static gate: a clean package passes and each malicious package fails by name.

Fits into the Hive:
    Mirrors src/hivemind/royal_jelly/quarantine_comb/gates/static.py
    (codingrules section 3). Test infrastructure, not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - src/hivemind/royal_jelly/quarantine_comb/gates/static.py for the module under test.
"""

from __future__ import annotations

import pytest
from builders.royal_jelly import (
    MALICIOUS_KINDS,
    make_gate_context,
    make_malicious_files,
    make_package_files,
    make_tool_files,
    make_tool_spec,
)

from hivemind.royal_jelly.quarantine_comb.gates import StaticGate
from hivemind.royal_jelly.spec import SPEC_FILE, TOOL_MODULE

# Each malicious kind and the text its failure reason must contain: the construct, by name.
_EXPECTED_REASON = {
    "socket_import": "socket",
    "from_subprocess": "subprocess",
    "os_system": "os.system",
    "os_alias": "os.system",
    "open_passwd": "open",
    "dunder_import": "__import__",
    "importlib": "importlib",
    "from_asyncio_subprocess": "create_subprocess_exec",
    "wrong_entry": "async def run",
    "sync_entry": "async def run",
    "not_stdlib": "numpy",
    "no_tests": "unittest.TestCase",
    "extra_file": "extra.py",
}


def test_the_table_covers_every_malicious_kind() -> None:
    assert set(_EXPECTED_REASON) == set(MALICIOUS_KINDS)


async def test_a_clean_package_passes() -> None:
    result = await StaticGate().run(make_gate_context())

    assert result.passed
    assert result.name == "static"


@pytest.mark.parametrize("kind", MALICIOUS_KINDS)
async def test_a_malicious_package_fails_and_the_reason_names_the_construct(kind: str) -> None:
    spec = make_tool_spec()
    ctx = make_gate_context(spec, files=make_malicious_files(kind, spec))

    result = await StaticGate().run(ctx)

    assert not result.passed
    assert _EXPECTED_REASON[kind] in result.reason


async def test_a_declared_dependency_is_refused_because_the_sandbox_cannot_install() -> None:
    spec = make_tool_spec(dependencies=("requests",))

    result = await StaticGate().run(make_gate_context(spec))

    assert not result.passed
    assert "the sandbox cannot install dependencies" in result.reason
    assert "requests" in result.reason


async def test_an_entry_point_other_than_tool_run_is_refused() -> None:
    spec = make_tool_spec(entry_point="tool:main")

    result = await StaticGate().run(make_gate_context(spec))

    assert not result.passed
    assert "tool:main" in result.reason


async def test_a_spec_json_that_differs_from_the_spec_is_refused() -> None:
    other = make_tool_spec(version="2.0.0")
    files = make_package_files(other)

    result = await StaticGate().run(make_gate_context(make_tool_spec(), files=files))

    assert not result.passed
    assert "spec.json" in result.reason


async def test_a_spec_json_that_is_not_a_spec_is_refused() -> None:
    spec = make_tool_spec()
    files = {**make_package_files(spec), SPEC_FILE: b"{not json"}

    result = await StaticGate().run(make_gate_context(spec, files=files))

    assert not result.passed
    assert "spec.json is not a valid ToolSpec" in result.reason


async def test_a_missing_file_is_refused() -> None:
    spec = make_tool_spec()
    files = make_package_files(spec)
    del files[TOOL_MODULE]

    result = await StaticGate().run(make_gate_context(spec, files=files))

    assert not result.passed
    assert "missing file 'tool.py'" in result.reason


async def test_a_syntax_error_is_a_finding_not_a_crash() -> None:
    spec = make_tool_spec()

    result = await StaticGate().run(
        make_gate_context(spec, files=make_tool_files("def (:\n", None, spec))
    )

    assert not result.passed
    assert "syntax error" in result.reason


async def test_every_problem_is_in_the_detail_not_only_the_first() -> None:
    source = (
        "import socket\nimport subprocess\n\nasync def run(session, arguments):\n    return {}\n"
    )

    spec = make_tool_spec()

    result = await StaticGate().run(
        make_gate_context(spec, files=make_tool_files(source, None, spec))
    )

    assert "socket" in result.detail
    assert "subprocess" in result.detail


async def test_non_utf8_source_is_refused() -> None:
    spec = make_tool_spec()
    files = {**make_package_files(spec), TOOL_MODULE: b"\xff\xfe\x00"}

    result = await StaticGate().run(make_gate_context(spec, files=files))

    assert not result.passed
    assert "UTF-8" in result.reason
