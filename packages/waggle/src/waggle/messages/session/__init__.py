"""Re-export the session family: the terminal over Waggle that drives a remote Real Cell.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). A session is
the terminal over Waggle through which a Warden (the always-on supervisor of one Cell) on the Hive
Stand drives a remote Real Cell, a borrowed device carrying a Pollen Packet (the thin gateway that
speaks the protocol). ``commands`` holds what the Warden sends (open, exec, stdin, close) and the
session enums; ``output`` what the device sends back (an output chunk and the exit); ``files`` the
file transfers in both directions. This package is the family's face: a caller imports any of its
messages, enums or value models from here without knowing which module defines them. The bounds
each module names stay in that module, because the spec makes the number normative, not the name.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Imported by waggle.messages (the catalogue and
    the package face) and by every bee that builds or reads a session payload; calls into
    nothing beyond its own modules.

Key invariants:
    - Every class the registry registers under ``session.*`` is re-exported here
      (tests/messages/test_registry_catalogue.py checks it).
    - This file holds re-exports and __all__ only; no message, enum or bound is defined here.

See Also:
    - docs/waggle/spec.md section 8.6 for the family's normative fields and rules.
    - waggle.messages.session.commands, waggle.messages.session.output and
      waggle.messages.session.files for the definitions.

Public API:
    - Commands (commands): EnvVar, ProcessSignal, SessionClose, SessionExec, SessionOpen,
      SessionOutcome, SessionRequestKind, SessionStdin, SessionStream.
    - Output (output): SessionExit, SessionOutput.
    - Files (files): SessionGetFile, SessionPutFile.
"""

from waggle.messages.session.commands import (
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

__all__ = [
    "EnvVar",
    "ProcessSignal",
    "SessionClose",
    "SessionExec",
    "SessionExit",
    "SessionGetFile",
    "SessionOpen",
    "SessionOutcome",
    "SessionOutput",
    "SessionPutFile",
    "SessionRequestKind",
    "SessionStdin",
    "SessionStream",
]
