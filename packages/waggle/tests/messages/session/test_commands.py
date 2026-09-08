"""Tests for the session family: EXAMPLES of all eight classes, and waggle.messages.session.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Holds EXAMPLES, one valid instance of every
    session class across the family's three modules, which the registry step later loads by
    file path to seed its per-family checks; pins for every class construction, the JSON round
    trip (bytes included), the rejection of an extra field, the lease id kind and the shared
    reason and chunk bounds; then the four enums, EnvVar and every rule spec section 8.6 names
    for SessionOpen, SessionExec, SessionStdin and SessionClose. The other four classes' own
    rules are pinned in test_output.py and test_files.py.

Key invariants:
    - EXAMPLES holds exactly one instance of each of the eight session classes.

See Also:
    - waggle.messages.session.commands, output and files for the modules under test.
    - docs/waggle/spec.md section 8.6 for the fields, bounds and validators pinned here.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from waggle.clock import FakeClock
from waggle.ids import IdKind, new_id
from waggle.messages.base import (
    DEFAULT_MAX_OUTPUT_BYTES,
    MAX_CHUNK_BYTES,
    MAX_PATH_CHARS,
    MAX_REASON_CHARS,
    WaggleMessage,
)
from waggle.messages.session.commands import (
    MAX_ARGV_ITEM_CHARS,
    MAX_ARGV_ITEMS,
    MAX_ARGV_TOTAL_CHARS,
    MAX_ENV_NAME_CHARS,
    MAX_ENV_VALUE_CHARS,
    MAX_ENV_VARS,
    EnvVar,
    ProcessSignal,
    SessionClose,
    SessionExec,
    SessionOpen,
    SessionOutcome,
    SessionRequestKind,
    SessionStdin,
    SessionStream,
)
from waggle.messages.session.files import SessionGetFile, SessionPutFile
from waggle.messages.session.output import SessionExit, SessionOutput

CLOCK = FakeClock()
LEASE_ID = new_id(IdKind.LEASE, CLOCK)
REQUEST_ID = new_id(IdKind.MESSAGE, CLOCK)
DIGEST = "0123456789abcdef" * 4  # 32 bytes of digest as 64 lowercase hex characters.
SESSION_CLASSES: tuple[type[WaggleMessage], ...] = (
    SessionOpen,
    SessionExec,
    SessionStdin,
    SessionOutput,
    SessionExit,
    SessionPutFile,
    SessionGetFile,
    SessionClose,
)

# One valid instance of every session class, in catalogue order; the registry step loads this
# tuple by file path, so its name and shape are part of the contract.
EXAMPLES: tuple[WaggleMessage, ...] = (
    SessionOpen(lease_id=LEASE_ID, reason="A task was placed on this Cell."),
    SessionExec(
        lease_id=LEASE_ID,
        argv=("python", "-c", "print('hi')"),
        cwd=None,
        env=(EnvVar(name="HIVE_TASK", value="task-plan"),),
        timeout_s=30.0,
        task_id=new_id(IdKind.TASK, CLOCK),
        worker_id=new_id(IdKind.WORKER, CLOCK),
    ),
    SessionStdin(lease_id=LEASE_ID, exec_id=REQUEST_ID, chunk=b"y\n", offset=0, signal=None),
    SessionOutput(
        lease_id=LEASE_ID,
        request_id=REQUEST_ID,
        stream=SessionStream.STDOUT,
        chunk=b"hi\n",
        offset=0,
        final=True,
    ),
    SessionExit(
        lease_id=LEASE_ID,
        request_id=REQUEST_ID,
        request_kind=SessionRequestKind.EXEC,
        outcome=SessionOutcome.EXITED,
        exit_code=0,
        signal_name=None,
        duration_s=0.5,
        size_bytes=None,
        sha256=None,
        reason="The process exited on its own.",
    ),
    SessionPutFile(
        lease_id=LEASE_ID,
        transfer_id=REQUEST_ID,
        total_bytes=3,
        path="notes/plan.md",
        chunk=b"abc",
        offset=0,
        final=True,
        sha256=DIGEST,
        task_id=None,
        worker_id=None,
    ),
    SessionGetFile(lease_id=LEASE_ID, path="/var/log/hive.log", task_id=None, worker_id=None),
    SessionClose(lease_id=LEASE_ID, reason="The lease is being released."),
)


def _rebuild(example: WaggleMessage, **changes: object) -> WaggleMessage:
    """Re-validate ``example`` with some fields replaced."""
    return type(example).model_validate({**example.model_dump(), **changes})


def _example(message_type: type[WaggleMessage]) -> WaggleMessage:
    """The EXAMPLES entry of ``message_type``."""
    return next(example for example in EXAMPLES if type(example) is message_type)


# ──────────────────────────────────────────────────────────────────────────────
# Every class
# ──────────────────────────────────────────────────────────────────────────────


def test_examples_hold_exactly_one_instance_of_every_session_class() -> None:
    assert tuple(type(example) for example in EXAMPLES) == SESSION_CLASSES


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda example: type(example).__name__)
def test_session_message_round_trips_and_is_frozen(example: WaggleMessage) -> None:
    assert type(example).model_validate(example.model_dump(mode="json")) == example
    with pytest.raises(ValidationError, match="frozen"):
        example.reason = "changed"  # type: ignore[attr-defined]  # The assignment is the test.


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda example: type(example).__name__)
def test_session_message_rejects_an_extra_field_and_a_foreign_lease_id(
    example: WaggleMessage,
) -> None:
    with pytest.raises(ValidationError, match="extra"):
        _rebuild(example, hop_count=1)
    with pytest.raises(ValidationError, match="lease_"):
        _rebuild(example, lease_id=new_id(IdKind.CELL, CLOCK))


@pytest.mark.parametrize(
    "example", [_example(SessionOpen), _example(SessionExit), _example(SessionClose)]
)
def test_reason_is_bounded_by_the_shared_limit(example: WaggleMessage) -> None:
    assert _rebuild(example, reason="x" * MAX_REASON_CHARS)
    with pytest.raises(ValidationError, match=f"at most {MAX_REASON_CHARS}"):
        _rebuild(example, reason="x" * (MAX_REASON_CHARS + 1))


@pytest.mark.parametrize("example", [_example(SessionStdin), _example(SessionOutput)])
def test_chunk_is_bounded_by_the_shared_chunk_limit(example: WaggleMessage) -> None:
    # SessionPutFile's chunk is pinned in test_files.py, where its total can grow too.
    assert _rebuild(example, chunk=b"x" * MAX_CHUNK_BYTES)
    with pytest.raises(ValidationError, match=f"at most {MAX_CHUNK_BYTES} bytes"):
        _rebuild(example, chunk=b"x" * (MAX_CHUNK_BYTES + 1))


# ──────────────────────────────────────────────────────────────────────────────
# Enums and EnvVar
# ──────────────────────────────────────────────────────────────────────────────


def test_session_enums_list_exactly_the_spec_members() -> None:
    assert [member.value for member in SessionStream] == ["STDOUT", "STDERR", "FILE"]
    assert [member.value for member in SessionOutcome] == [
        "EXITED",
        "SIGNALED",
        "TIMED_OUT",
        "KILLED",
        "COMPLETED",
    ]
    assert [member.value for member in ProcessSignal] == ["INTERRUPT", "TERMINATE", "KILL"]
    assert [member.value for member in SessionRequestKind] == [
        "OPEN",
        "EXEC",
        "PUT_FILE",
        "GET_FILE",
    ]
    for enum in (SessionStream, SessionOutcome, ProcessSignal, SessionRequestKind):
        assert all(member.name == member.value for member in enum)


def test_env_var_round_trips_and_hides_its_value_from_the_repr() -> None:
    variable = EnvVar(name="API_TOKEN", value="hunter2")

    assert EnvVar.model_validate(variable.model_dump(mode="json")) == variable
    assert "API_TOKEN" in repr(variable)
    assert "hunter2" not in repr(variable)


@pytest.mark.parametrize("name", ["1A", "A-B", "A B", "", "x" * (MAX_ENV_NAME_CHARS + 1)])
def test_env_var_rejects_a_name_outside_the_identifier_shape(name: str) -> None:
    with pytest.raises(ValidationError, match="name"):
        EnvVar(name=name, value="x")


def test_env_var_bounds_the_value_and_rejects_an_extra_field() -> None:
    assert EnvVar(name="A", value="x" * MAX_ENV_VALUE_CHARS)
    with pytest.raises(ValidationError, match=f"at most {MAX_ENV_VALUE_CHARS}"):
        EnvVar(name="A", value="x" * (MAX_ENV_VALUE_CHARS + 1))
    with pytest.raises(ValidationError, match="extra"):
        EnvVar.model_validate({"name": "A", "value": "x", "exported": True})


# ──────────────────────────────────────────────────────────────────────────────
# SessionExec
# ──────────────────────────────────────────────────────────────────────────────


def test_session_exec_defaults_stdin_closed_and_the_output_cap() -> None:
    exec_ = _example(SessionExec)

    assert isinstance(exec_, SessionExec)
    assert exec_.has_stdin is False
    assert exec_.max_output_bytes == DEFAULT_MAX_OUTPUT_BYTES
    assert exec_.model_dump(mode="json")["env"] == [{"name": "HIVE_TASK", "value": "task-plan"}]


def test_session_exec_accepts_a_command_line_at_every_limit() -> None:
    at_total = _rebuild(
        _example(SessionExec),
        argv=("x" * MAX_ARGV_ITEM_CHARS,) * (MAX_ARGV_TOTAL_CHARS // MAX_ARGV_ITEM_CHARS),
        env=tuple({"name": f"V{index}", "value": "1"} for index in range(MAX_ENV_VARS)),
        cwd="x" * MAX_PATH_CHARS,
        max_output_bytes=1,
        task_id=None,
        worker_id=None,
    )

    assert isinstance(at_total, SessionExec)
    assert sum(len(item) for item in at_total.argv) == MAX_ARGV_TOTAL_CHARS
    assert len(at_total.env) == MAX_ENV_VARS


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"argv": ()}, "at least 1"),
        ({"argv": ("x",) * (MAX_ARGV_ITEMS + 1)}, f"at most {MAX_ARGV_ITEMS}"),
        ({"argv": ("x" * (MAX_ARGV_ITEM_CHARS + 1),)}, f"at most {MAX_ARGV_ITEM_CHARS}"),
        (
            {
                "argv": ("x" * MAX_ARGV_ITEM_CHARS,)
                * (MAX_ARGV_TOTAL_CHARS // MAX_ARGV_ITEM_CHARS + 1)
            },
            f"more than the {MAX_ARGV_TOTAL_CHARS}",
        ),
        ({"cwd": "x" * (MAX_PATH_CHARS + 1)}, f"at most {MAX_PATH_CHARS}"),
        ({"env": ({"name": "A", "value": "1"},) * (MAX_ENV_VARS + 1)}, f"at most {MAX_ENV_VARS}"),
        ({"env": ({"name": "A", "value": "1"}, {"name": "A", "value": "2"})}, "unique"),
        ({"timeout_s": 0}, "greater than 0"),
        ({"max_output_bytes": 0}, "greater than or equal to 1"),
        ({"task_id": "cell_01ARZ3NDEKTSV4RRFFQ69G5FAV"}, "task_"),
        ({"worker_id": "task_01ARZ3NDEKTSV4RRFFQ69G5FAV"}, "worker_"),
    ],
)
def test_session_exec_validators(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(_example(SessionExec), **changes)


# ──────────────────────────────────────────────────────────────────────────────
# SessionStdin
# ──────────────────────────────────────────────────────────────────────────────


def test_session_stdin_hides_the_chunk_from_the_repr_and_defaults_final_false() -> None:
    stdin = _example(SessionStdin)

    assert isinstance(stdin, SessionStdin)
    assert stdin.final is False
    assert "chunk" not in repr(stdin)
    assert "exec_id" in repr(stdin)


def test_session_stdin_signal_requires_an_empty_chunk() -> None:
    interrupt = _rebuild(_example(SessionStdin), chunk=b"", signal="INTERRUPT", final=True)

    assert isinstance(interrupt, SessionStdin)
    assert interrupt.signal is ProcessSignal.INTERRUPT
    assert interrupt.chunk == b""
    with pytest.raises(ValidationError, match="must carry an empty chunk"):
        _rebuild(_example(SessionStdin), signal="KILL")


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"offset": -1}, "greater than or equal to 0"),
        ({"signal": "SIGKILL"}, "signal"),
        ({"exec_id": "lease_01ARZ3NDEKTSV4RRFFQ69G5FAV"}, "msg_"),
    ],
)
def test_session_stdin_validators(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(_example(SessionStdin), **changes)


# ──────────────────────────────────────────────────────────────────────────────
# SessionOpen and SessionClose
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("message_type", [SessionOpen, SessionClose])
def test_session_open_and_close_carry_only_the_lease_and_a_reason(
    message_type: type[WaggleMessage],
) -> None:
    assert set(message_type.model_fields) == {"lease_id", "reason"}
    assert _example(message_type).model_dump(mode="json")["lease_id"] == LEASE_ID
