"""Define the session family's device-sent messages: the output chunk and the exit.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance), and the
session family is terminal-over-Waggle: a Warden (the always-on supervisor of one Cell, a unit of
compute) drives a remote Real Cell (an existing device borrowed for a task and left exactly as
found) through its Pollen Packet (the thin gateway on an enrolled device). The two messages here are
what the packet sends back, split out of ``waggle.messages.session.commands`` by responsibility so
each file stays under the codingrules 5.1 size limit: ``SessionOutput`` streams one chunk of an
exec's stdout or stderr, or of a file being read, and ``SessionExit`` is the terminal event of every
session request (an open, an exec, a put-file or a get-file), the one message the session rule of
spec section 3 says each request ends in. Because an exit's ``request_kind`` is on the model, every
rule about which fields it carries is decidable without the request it answers. Every bound is a
named constant here; the number, not the name, is normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Registered by waggle.messages.registry, which maps
    each class to its kind; built by a Pollen Packet and read by a Warden; calls into
    waggle.messages.base and waggle.messages.session.commands (the family's enums) only.

Key invariants:
    - No class here carries its kind string; the registry is the only place kinds live.
    - Every message is frozen and forbids extras through WaggleMessage's config.
    - Every rule the spec marks (validator) is a pydantic validator on the class; every rule it
      marks (receiver rule) is deliberately absent, because the receiver enforces it.
    - A chunk is at most MAX_CHUNK_BYTES and travels with offset and final (spec section 5).
    - A SessionExit's outcome, exit_code, signal_name, size_bytes and sha256 are each present
      exactly for the request kinds and outcomes the spec names, never otherwise.

See Also:
    - docs/waggle/spec.md section 8.6 for the normative fields, bounds and validators.
    - docs/waggle/spec.md section 3 (the session rule) for which exit answers which request.
    - waggle.messages.session.commands for the enums, EnvVar and the four Warden-sent messages.
    - waggle.messages.session.files for the transfers whose exits carry a size and digest.
    - waggle.messages.registry for the kinds these classes are registered under.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, model_validator

from waggle.messages.base import (
    MAX_CHUNK_BYTES,
    MAX_REASON_CHARS,
    SHA256_PATTERN,
    LeaseIdField,
    MessageIdField,
    WaggleMessage,
)
from waggle.messages.session.commands import SessionOutcome, SessionRequestKind, SessionStream

MAX_SIGNAL_NAME_CHARS = 16  # SIGKILL, SIGTERM, CTRL_BREAK_EVENT: a platform signal's short name.

__all__ = ["MAX_SIGNAL_NAME_CHARS", "SessionExit", "SessionOutput"]

# The outcomes only a process can end with; an open or a transfer only ever COMPLETES.
_EXEC_OUTCOMES = frozenset(
    {
        SessionOutcome.EXITED,
        SessionOutcome.SIGNALED,
        SessionOutcome.TIMED_OUT,
        SessionOutcome.KILLED,
    }
)
# The requests that move a whole file, so their exit carries the file's size and digest.
_FILE_REQUEST_KINDS = frozenset({SessionRequestKind.PUT_FILE, SessionRequestKind.GET_FILE})

# A reason field, as the catalogue conventions fix it: always named `reason`, always bounded by
# the shared MAX_REASON_CHARS, so the Pheromone Trail (the append-only audit log) records why.
_Reason = Annotated[str, Field(max_length=MAX_REASON_CHARS)]


class SessionOutput(WaggleMessage):
    """One chunk of an exec's stdout or stderr, or of a file being read (session.output).

    An event correlated to the SessionExec or SessionGetFile it answers; the stream's chunks
    precede that request's SessionExit and never follow it (spec section 3).
    """

    lease_id: LeaseIdField = Field(description="The session the chunk belongs to.")
    request_id: MessageIdField = Field(
        description="Envelope id of the SessionExec or SessionGetFile this chunk answers; also "
        "the envelope's correlation_id. A chunk whose request_id is not an outstanding request "
        "of the session is dropped (receiver rule)."
    )
    stream: SessionStream = Field(
        description="Which stream; FILE only for a get-file transfer (receiver rule)."
    )
    chunk: bytes = Field(max_length=MAX_CHUNK_BYTES, description="The bytes, in stream order.")
    offset: int = Field(ge=0, description="Byte offset within its stream.")
    final: bool = Field(
        default=False,
        description="True on the last chunk of this stream; stdout and stderr each end with "
        "their own final chunk, before the SessionExit.",
    )


class SessionExit(WaggleMessage):
    """The terminal event of one session request (session.exit, an event).

    An exec's exit status, or the completion of an open, put-file or get-file; request_kind
    says which, so every rule below is decidable on the model alone.
    """

    lease_id: LeaseIdField = Field(description="The session the request ran in.")
    request_id: MessageIdField = Field(
        description="Envelope id of the request this ends (for a put-file, its transfer_id); "
        "also the envelope's correlation_id."
    )
    request_kind: SessionRequestKind = Field(description="Which request this ends.")
    outcome: SessionOutcome = Field(
        description="How it ended; EXITED, SIGNALED, TIMED_OUT and KILLED only when "
        "request_kind is EXEC, COMPLETED only otherwise."
    )
    exit_code: int | None = Field(
        description="The process's exit status; set exactly when outcome is EXITED."
    )
    signal_name: Annotated[str, Field(max_length=MAX_SIGNAL_NAME_CHARS)] | None = Field(
        description="The platform signal that ended the process; set exactly when outcome is "
        "SIGNALED."
    )
    duration_s: float = Field(ge=0, description="Seconds from start to end.")
    size_bytes: Annotated[int, Field(ge=0)] | None = Field(
        description="Total bytes written or sent; set exactly when request_kind is PUT_FILE or "
        "GET_FILE."
    )
    sha256: Annotated[str, Field(pattern=SHA256_PATTERN)] | None = Field(
        description="Digest of the whole file written or read; set exactly when request_kind "
        "is PUT_FILE or GET_FILE."
    )
    reason: _Reason = Field(description="Why it ended, in words.")

    @model_validator(mode="after")
    def _outcome_matches_request_kind(self) -> SessionExit:
        """Allow a process's four outcomes only for EXEC, and COMPLETED only for the rest."""
        # An open or a transfer has no process to exit or be signalled, and an exec never merely
        # completes: request_kind alone decides the outcome set, so a mismatch is a sender bug.
        if (self.request_kind is SessionRequestKind.EXEC) != (self.outcome in _EXEC_OUTCOMES):
            raise ValueError(
                f"SessionExit outcome {self.outcome.value} does not fit request_kind "
                f"{self.request_kind.value}: EXEC ends EXITED, SIGNALED, TIMED_OUT or KILLED, "
                "every other request ends COMPLETED."
            )
        return self

    @model_validator(mode="after")
    def _process_fields_match_outcome(self) -> SessionExit:
        """Require exit_code exactly for EXITED and signal_name exactly for SIGNALED."""
        # Each field describes one way a process ends; carrying it for any other outcome would
        # invite a reader to trust a status that never happened.
        _check_set_exactly_when(
            "exit_code", self.exit_code, self.outcome is SessionOutcome.EXITED, "outcome is EXITED"
        )
        _check_set_exactly_when(
            "signal_name",
            self.signal_name,
            self.outcome is SessionOutcome.SIGNALED,
            "outcome is SIGNALED",
        )
        return self

    @model_validator(mode="after")
    def _file_fields_match_request_kind(self) -> SessionExit:
        """Require size_bytes and sha256 exactly for a put-file or get-file."""
        # The receiver verifies a transfer against these two figures, so a transfer must carry
        # them; an open or an exec moved no file, so for them the figures would be invented.
        is_file = self.request_kind in _FILE_REQUEST_KINDS
        when = "request_kind is PUT_FILE or GET_FILE"
        _check_set_exactly_when("size_bytes", self.size_bytes, is_file, when)
        _check_set_exactly_when("sha256", self.sha256, is_file, when)
        return self


def _check_set_exactly_when(field: str, value: object, is_expected: bool, when: str) -> None:
    """Raise ValueError unless ``value`` is non-None exactly when ``is_expected`` holds."""
    # One helper for every "set exactly when" rule of SessionExit, so each rule's message names
    # the field, its actual state and the condition in the same words.
    if (value is not None) != is_expected:
        state = "is set" if value is not None else "is None"
        raise ValueError(f"SessionExit {field} {state} but must be set exactly when {when}.")
