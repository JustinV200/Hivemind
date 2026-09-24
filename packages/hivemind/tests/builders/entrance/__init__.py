"""Build hivemind.entrance test data: records, an enrolment rig, and a login rig over it.

The face of the Entrance's builders (codingrules 14.5): ``records`` (devices, invites, events and
store walks), ``enrolment`` (the ``Enrolment`` rig over real stores, driving devices through the
real enrolment flows) and ``auth`` (the ``AuthRig`` over it, logging devices in and signing
requests as a program or a browser would, plus the Entrance Reducer's recording seams). A test
writes ``from builders.entrance import make_device``, whichever module the builder lives in.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the tests under
    packages/hivemind/tests/unit/entrance and the Entrance store contract suites.

Key invariants:
    - This file holds re-exports and ``__all__`` only.

See Also:
    - builders.entrance.records, builders.entrance.enrolment and builders.entrance.auth.
"""

from builders.entrance.auth import (
    LOOPBACK,
    PASSWORD,
    REMOTE,
    REMOTE_ORIGIN,
    REMOTE_RELYING_PARTY,
    SESSION_RULES,
    WRONG_PASSWORD,
    AuthRig,
    BrowserKey,
    CountingHasher,
    RecordingListener,
    RecordingStreams,
    admitted_console,
    auth_rig,
    browser_login,
    cheap_password_hash,
    program_login,
    session_book,
    sign_b64url,
    signed_request,
    socket_opening,
)
from builders.entrance.enrolment import (
    Enrolment,
    Fakes,
    admitted,
    admitted_browser,
    admitted_program,
    approval,
    ed25519_proof,
    enrolment_over,
    memory_enrolment,
    mint,
    redeem_browser,
    redeem_program,
    sqlite_enrolment,
)
from builders.entrance.records import (
    ADDRESS,
    APPROVAL_TTL,
    INVITE_TTL,
    ORIGIN,
    PATHS,
    PENDING_TTL,
    RELYING_PARTY,
    STORE_IDENTITY,
    approve_changes,
    changes_for,
    ed25519_public_key,
    edge_event,
    entry_event,
    make_description,
    make_device,
    make_identity,
    make_invite,
    make_pending,
    make_session,
    redeem_changes,
    walk_to,
)

__all__ = [
    "ADDRESS",
    "APPROVAL_TTL",
    "INVITE_TTL",
    "LOOPBACK",
    "ORIGIN",
    "PASSWORD",
    "PATHS",
    "PENDING_TTL",
    "RELYING_PARTY",
    "REMOTE",
    "REMOTE_ORIGIN",
    "REMOTE_RELYING_PARTY",
    "SESSION_RULES",
    "STORE_IDENTITY",
    "WRONG_PASSWORD",
    "AuthRig",
    "BrowserKey",
    "CountingHasher",
    "Enrolment",
    "Fakes",
    "RecordingListener",
    "RecordingStreams",
    "admitted",
    "admitted_browser",
    "admitted_console",
    "admitted_program",
    "approval",
    "approve_changes",
    "auth_rig",
    "browser_login",
    "changes_for",
    "cheap_password_hash",
    "ed25519_proof",
    "ed25519_public_key",
    "edge_event",
    "enrolment_over",
    "entry_event",
    "make_description",
    "make_device",
    "make_identity",
    "make_invite",
    "make_pending",
    "make_session",
    "memory_enrolment",
    "mint",
    "program_login",
    "redeem_browser",
    "redeem_changes",
    "redeem_program",
    "session_book",
    "sign_b64url",
    "signed_request",
    "socket_opening",
    "sqlite_enrolment",
    "walk_to",
]
