"""Tests for hivemind.workers.roles.drone.outcome.records: ToolCallRecord and classify_error.

Fits into the Hive:
    Mirrors src/hivemind/workers/roles/drone/outcome/records.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.roles.drone.outcome.records for the module under test.
"""

from __future__ import annotations

from builders.llm import make_tool_call

from hivemind.workers.roles.drone.outcome.records import classify_error, target_for


def test_classify_error_reads_a_verified_capped_result_as_success() -> None:
    content = "state=VERIFIED; reason=applied; checks=(ALLOWLIST=PASSED); postconditions=(none)"

    assert classify_error("write_file", content) is False


def test_classify_error_reads_a_rejected_capped_result_as_failure() -> None:
    content = "state=REJECTED; reason=blocked; checks=(ALLOWLIST=FAILED); postconditions=(none)"

    assert classify_error("write_file", content) is True


def test_classify_error_reads_a_rolled_back_capped_result_as_failure() -> None:
    content = "state=ROLLED_BACK; reason=postcondition failed; checks=(none); postconditions=(none)"

    assert classify_error("run_command", content) is True


def test_classify_error_reads_a_pre_proposal_validation_string_as_failure() -> None:
    """An arg check failed before a Proposal existed: never reaches describe()'s "state=" token."""
    assert classify_error("write_file", "path must be a non-empty string.") is True


def test_classify_error_reads_a_known_fixed_template_as_failure() -> None:
    assert classify_error("read_file", "no file at 'missing.txt'.") is True
    refused = "refused by the Guard (guard.not_held): Worker w was refused it. Nothing was done."
    assert classify_error("read_file", refused) is True
    assert classify_error("ask", refused) is True
    assert classify_error("http_request", refused) is True
    assert classify_error("ask", "no tool named 'ask' is offered.") is True


def test_classify_error_never_flags_a_free_form_answer_as_a_failure() -> None:
    """`ask`'s own Answer text is free-form human/Warden wording, not one of the fixed templates."""
    assert classify_error("ask", "Yes, go ahead and use the staging environment.") is False


def test_classify_error_never_flags_ordinary_file_content_as_a_failure() -> None:
    assert classify_error("read_file", "line one\nline two\n") is False


def test_target_for_names_a_write_files_own_path() -> None:
    call = make_tool_call(name="write_file", arguments={"path": "scratch/out.txt", "content": "x"})

    assert target_for(call) == "scratch/out.txt"


def test_target_for_joins_a_run_commands_own_argv() -> None:
    call = make_tool_call(name="run_command", arguments={"argv": ["pytest", "-q"]})

    assert target_for(call) == "pytest -q"


def test_target_for_names_an_http_requests_own_url() -> None:
    call = make_tool_call(name="http_request", arguments={"method": "GET", "url": "https://x"})

    assert target_for(call) == "https://x"


def test_target_for_falls_back_to_the_tool_name_for_an_untargeted_call() -> None:
    call = make_tool_call(name="ask", arguments={"text": "Which environment?"})

    assert target_for(call) == "ask"
