"""Tests for waggle.messages.session_output: SessionOutput and SessionExit.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Pins for both classes construction with every
    stream or outcome, the base64 round trip of a chunk holding every byte value, the bounds,
    and every validator spec section 8.6 names for SessionExit: the outcome-against-request_kind
    matrix in full, and the exit_code, signal_name, size_bytes and sha256 exactly-when rules in
    both directions. The family's EXAMPLES live in test_session.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.messages.session_output for the module under test.
    - docs/waggle/spec.md section 8.6 for the fields, bounds and validators pinned here.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from waggle.clock import FakeClock
from waggle.ids import IdKind, new_id
from waggle.messages.base import MAX_CHUNK_BYTES
from waggle.messages.session import SessionOutcome, SessionRequestKind, SessionStream
from waggle.messages.session_output import MAX_SIGNAL_NAME_CHARS, SessionExit, SessionOutput

CLOCK = FakeClock()
LEASE_ID = new_id(IdKind.LEASE, CLOCK)
REQUEST_ID = new_id(IdKind.MESSAGE, CLOCK)
DIGEST = "0123456789abcdef" * 4  # 32 bytes of digest as 64 lowercase hex characters.
FILE_KINDS = (SessionRequestKind.PUT_FILE, SessionRequestKind.GET_FILE)


def _output(**overrides: object) -> SessionOutput:
    """A final stdout chunk, then ``overrides``."""
    fields: dict[str, object] = {
        "lease_id": LEASE_ID,
        "request_id": REQUEST_ID,
        "stream": SessionStream.STDOUT,
        "chunk": b"hi\n",
        "offset": 0,
        "final": True,
    }
    return SessionOutput.model_validate({**fields, **overrides})


def _exit(**overrides: object) -> SessionExit:
    """An exec's clean exit, then ``overrides``."""
    fields: dict[str, object] = {
        "lease_id": LEASE_ID,
        "request_id": REQUEST_ID,
        "request_kind": SessionRequestKind.EXEC,
        "outcome": SessionOutcome.EXITED,
        "exit_code": 0,
        "signal_name": None,
        "duration_s": 1.5,
        "size_bytes": None,
        "sha256": None,
        "reason": "The process exited on its own.",
    }
    return SessionExit.model_validate({**fields, **overrides})


def _fitting_fields(kind: SessionRequestKind, outcome: SessionOutcome) -> dict[str, object]:
    """The exit_code, signal_name, size_bytes and sha256 that fit ``kind`` and ``outcome``."""
    is_file = kind in FILE_KINDS
    return {
        "exit_code": 0 if outcome is SessionOutcome.EXITED else None,
        "signal_name": "SIGTERM" if outcome is SessionOutcome.SIGNALED else None,
        "size_bytes": 3 if is_file else None,
        "sha256": DIGEST if is_file else None,
    }


# ──────────────────────────────────────────────────────────────────────────────
# SessionOutput
# ──────────────────────────────────────────────────────────────────────────────


def test_session_output_constructs_with_every_stream_and_defaults_final_false() -> None:
    for stream in SessionStream:
        assert _output(stream=stream).stream is stream
    assert _output(final=False).final is False
    assert (
        SessionOutput.model_validate(
            {
                "lease_id": LEASE_ID,
                "request_id": REQUEST_ID,
                "stream": "STDERR",
                "chunk": b"",
                "offset": 0,
            }
        ).final
        is False
    )


def test_session_output_round_trips_every_byte_value_through_base64() -> None:
    output = _output(chunk=bytes(range(256)), offset=MAX_CHUNK_BYTES)

    wire = output.model_dump(mode="json")

    assert isinstance(wire["chunk"], str)
    assert SessionOutput.model_validate(wire) == output


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"chunk": b"x" * (MAX_CHUNK_BYTES + 1)}, f"at most {MAX_CHUNK_BYTES} bytes"),
        ({"offset": -1}, "greater than or equal to 0"),
        ({"stream": "FILES"}, "stream"),
        ({"request_id": LEASE_ID}, "msg_"),
        ({"lease_id": REQUEST_ID}, "lease_"),
        ({"encoding": "utf-8"}, "extra"),
    ],
)
def test_session_output_validators(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _output(**changes)


# ──────────────────────────────────────────────────────────────────────────────
# SessionExit
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("outcome", list(SessionOutcome))
@pytest.mark.parametrize("kind", list(SessionRequestKind))
def test_session_exit_allows_process_outcomes_for_exec_alone_and_completed_for_the_rest(
    kind: SessionRequestKind, outcome: SessionOutcome
) -> None:
    fits = (kind is SessionRequestKind.EXEC) == (outcome is not SessionOutcome.COMPLETED)
    fields = {"request_kind": kind, "outcome": outcome, **_fitting_fields(kind, outcome)}

    if fits:
        assert _exit(**fields).outcome is outcome
    else:
        with pytest.raises(ValidationError, match="does not fit request_kind"):
            _exit(**fields)


def test_session_exit_round_trips_each_consistent_shape() -> None:
    shapes = (
        _exit(),
        _exit(outcome="SIGNALED", exit_code=None, signal_name="SIGTERM"),
        _exit(outcome="TIMED_OUT", exit_code=None),
        _exit(outcome="KILLED", exit_code=None, duration_s=0),
        _exit(request_kind="OPEN", outcome="COMPLETED", exit_code=None),
        _exit(
            request_kind="PUT_FILE",
            outcome="COMPLETED",
            exit_code=None,
            size_bytes=0,
            sha256=DIGEST,
        ),
        _exit(
            request_kind="GET_FILE",
            outcome="COMPLETED",
            exit_code=None,
            size_bytes=3,
            sha256=DIGEST,
        ),
    )

    for shape in shapes:
        assert SessionExit.model_validate(shape.model_dump(mode="json")) == shape


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"exit_code": None}, "exit_code is None but must be set exactly when outcome is EXITED"),
        ({"outcome": "KILLED"}, "exit_code is set but must be set exactly when outcome is EXITED"),
        ({"outcome": "SIGNALED", "exit_code": None}, "signal_name is None"),
        ({"signal_name": "SIGTERM"}, "signal_name is set"),
        (
            {
                "outcome": "SIGNALED",
                "exit_code": None,
                "signal_name": "S" * (MAX_SIGNAL_NAME_CHARS + 1),
            },
            f"at most {MAX_SIGNAL_NAME_CHARS}",
        ),
        ({"size_bytes": 3}, "size_bytes is set"),
        ({"sha256": DIGEST}, "sha256 is set"),
        (
            {
                "request_kind": "GET_FILE",
                "outcome": "COMPLETED",
                "exit_code": None,
                "sha256": DIGEST,
            },
            "size_bytes is None but must be set exactly when request_kind is PUT_FILE or GET_FILE",
        ),
        (
            {
                "request_kind": "PUT_FILE",
                "outcome": "COMPLETED",
                "exit_code": None,
                "size_bytes": 3,
            },
            "sha256 is None",
        ),
        (
            {
                "request_kind": "PUT_FILE",
                "outcome": "COMPLETED",
                "exit_code": None,
                "size_bytes": -1,
                "sha256": DIGEST,
            },
            "greater than or equal to 0",
        ),
        (
            {
                "request_kind": "GET_FILE",
                "outcome": "COMPLETED",
                "exit_code": None,
                "size_bytes": 3,
                "sha256": DIGEST.upper(),
            },
            "pattern",
        ),
        ({"duration_s": -0.1}, "greater than or equal to 0"),
        ({"request_kind": "RUN"}, "request_kind"),
        ({"request_id": LEASE_ID}, "msg_"),
        ({"lease_id": REQUEST_ID}, "lease_"),
        ({"stderr_tail": "boom"}, "extra"),
    ],
)
def test_session_exit_validators(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _exit(**changes)
