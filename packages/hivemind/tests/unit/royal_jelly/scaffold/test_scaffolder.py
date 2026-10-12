"""Tests for hivemind.royal_jelly.scaffold.scaffolder: a scripted model writes, the Hive checks.

Fits into the Hive:
    Mirrors src/hivemind/royal_jelly/scaffold/scaffolder.py (codingrules section 3). Every model
    call goes to a scripted `FakeLLMProvider` (codingrules 14.3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.royal_jelly.scaffold.scaffolder for the module under test.
"""

from __future__ import annotations

import json

import pytest
from builders.llm import make_bound
from builders.royal_jelly import make_brief, make_failing_report, make_tool_spec

from hivemind.forage.slots import ModelSlot
from hivemind.llm import (
    DirectCallGate,
    FakeLLMProvider,
    LLMResponse,
    ProviderCapabilities,
    text_response,
)
from hivemind.llm.errors import ProviderUnavailableError
from hivemind.royal_jelly.errors import PackageError, SpecRejectedError
from hivemind.royal_jelly.scaffold import ModelScaffolder, ScaffoldFailedError
from hivemind.royal_jelly.scaffold.models import ScaffoldReply
from hivemind.royal_jelly.scaffold.scaffolder import assemble
from hivemind.royal_jelly.spec import (
    SPEC_FILE,
    TESTS_MODULE,
    TOOL_MODULE,
    ToolSpec,
    sha256_of,
    unpack,
)
from waggle.clock import FakeClock
from waggle.messages.labels import OsFamily
from waggle.messages.tool.authoring import ToolScope

_SCHEMA = json.dumps(
    {"type": "object", "properties": {"text": {"type": "string"}}, "additionalProperties": False}
)
_TOOL_SOURCE = "async def run(session, arguments):\n    return {'echo': arguments}\n"
_TEST_SOURCE = (
    "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n"
)


def _reply(**overrides: object) -> LLMResponse:
    """Script one ScaffoldReply-shaped JSON reply."""
    fields: dict[str, object] = {
        "description": "Echo the text argument back.",
        "input_schema_json": _SCHEMA,
        "test_source": _TEST_SOURCE,
        "tool_source": _TOOL_SOURCE,
        "version": "1.0.0",
        "required_capabilities": ["net:example.org"],
        "notes": "",
    }
    fields.update(overrides)
    return text_response(json.dumps(fields))


def _scaffolder(provider: FakeLLMProvider) -> ModelScaffolder:
    bound = make_bound(slot=ModelSlot.SCAFFOLDER, binding="scaffolder", provider=provider)
    return ModelScaffolder(bound, DirectCallGate(), FakeClock())


async def test_scaffold_builds_a_validated_spec_and_the_three_file_package() -> None:
    provider = FakeLLMProvider()
    provider.script(_reply())
    brief = make_brief()

    result = await _scaffolder(provider).scaffold(brief)

    assert set(result.files) == {TOOL_MODULE, TESTS_MODULE, SPEC_FILE}
    assert result.files[TOOL_MODULE].decode() == _TOOL_SOURCE
    assert unpack(result.package) == result.files
    assert result.package_sha256 == sha256_of(result.package)
    assert result.package_size_bytes == len(result.package)
    assert ToolSpec.from_json(result.files[SPEC_FILE].decode()) == result.spec


async def test_the_spec_takes_every_identity_field_and_the_platform_from_the_brief() -> None:
    provider = FakeLLMProvider()
    provider.script(_reply())
    brief = make_brief(name="disk_report", scope=ToolScope.CELL, platform=make_brief().platform)

    result = await _scaffolder(provider).scaffold(brief)

    spec = result.spec
    assert (spec.name, spec.scope, spec.cell_id) == ("disk_report", ToolScope.CELL, brief.cell_id)
    assert (spec.tool_id, spec.requested_by) == (brief.tool_id, brief.requested_by)
    assert spec.supported_os == (OsFamily.LINUX,)
    assert spec.dependencies == ()
    assert spec.entry_point == "tool:run"


async def test_the_model_cannot_choose_its_own_name_scope_or_cell() -> None:
    provider = FakeLLMProvider()
    # Every attempt on every rung carries a field the reply model does not have.
    provider.script(*[_reply(name="other", scope="HIVE")] * 12)

    with pytest.raises(ScaffoldFailedError):
        await _scaffolder(provider).scaffold(make_brief())


async def test_a_spec_that_widens_the_briefs_capabilities_is_refused() -> None:
    provider = FakeLLMProvider()
    provider.script(_reply(required_capabilities=["net:example.org", "net:evil.example"]))

    with pytest.raises(SpecRejectedError) as raised:
        await _scaffolder(provider).scaffold(make_brief())

    assert any("net:evil.example" in reason for reason in raised.value.reasons)
    assert raised.value.tool_name == "echo_tool"


async def test_a_spec_that_asks_for_another_tool_is_refused() -> None:
    provider = FakeLLMProvider()
    provider.script(_reply(required_capabilities=["tool:run_command"]))
    brief = make_brief(required_capabilities=("tool:run_command",))

    with pytest.raises(SpecRejectedError, match="never grants itself other tools"):
        await _scaffolder(provider).scaffold(brief)


async def test_a_schema_that_allows_extra_properties_is_refused() -> None:
    provider = FakeLLMProvider()
    open_schema = json.dumps({"type": "object", "properties": {}})
    provider.script(_reply(input_schema_json=open_schema))

    with pytest.raises(SpecRejectedError, match="additionalProperties"):
        await _scaffolder(provider).scaffold(make_brief())


def test_assemble_turns_a_pydantic_failure_into_a_rejection_naming_the_field() -> None:
    reply = ScaffoldReply.model_validate(
        {
            "description": "Echo.",
            "input_schema_json": _SCHEMA,
            "test_source": _TEST_SOURCE,
            "tool_source": _TOOL_SOURCE,
        }
    )
    # model_copy skips validation on purpose: a brief that bypassed its own checks.
    brief = make_brief().model_copy(update={"name": "Bad Name"})

    with pytest.raises(SpecRejectedError) as raised:
        assemble(brief, reply)

    assert any(reason.startswith("name:") for reason in raised.value.reasons)


async def test_the_retry_brief_reaches_the_model_with_the_failing_gate() -> None:
    provider = FakeLLMProvider()
    provider.script(_reply())
    brief = make_brief().with_retry(
        make_failing_report(make_tool_spec(), "b" * 64, failing_gate="schema"), "old source"
    )

    await _scaffolder(provider).scaffold(brief)

    (request,) = provider.calls
    assert request.system is not None
    assert "Failing gate: schema" in request.system
    assert "old source" in request.system


async def test_an_unreachable_model_is_a_scaffold_failure() -> None:
    provider = FakeLLMProvider()
    provider.script(ProviderUnavailableError("fake", "down"))

    with pytest.raises(ScaffoldFailedError, match="ProviderUnavailableError"):
        await _scaffolder(provider).scaffold(make_brief())


async def test_a_model_that_never_answers_validly_is_a_scaffold_failure() -> None:
    provider = FakeLLMProvider(
        capabilities=ProviderCapabilities.full(), responder=lambda _request: text_response("nope")
    )

    with pytest.raises(ScaffoldFailedError, match="MalformedOutputError"):
        await _scaffolder(provider).scaffold(make_brief())


def test_assemble_refuses_source_that_cannot_be_written_as_utf8() -> None:
    reply = ScaffoldReply.model_validate(
        {
            "description": "Echo.",
            "input_schema_json": _SCHEMA,
            "test_source": _TEST_SOURCE,
            "tool_source": "x = '\\ud800'",
            "required_capabilities": [],
        }
    ).model_copy(update={"tool_source": "lone surrogate \ud800"})

    with pytest.raises(ScaffoldFailedError, match="UTF-8"):
        assemble(make_brief(), reply)


def test_assemble_refuses_a_package_over_the_size_cap() -> None:
    reply = ScaffoldReply.model_validate(
        {
            "description": "Echo.",
            "input_schema_json": _SCHEMA,
            "test_source": _TEST_SOURCE,
            "tool_source": "x" * 100,
        }
    ).model_copy(update={"tool_source": "x" * (5 * 1024 * 1024)})

    with pytest.raises(PackageError):
        assemble(make_brief(), reply)
