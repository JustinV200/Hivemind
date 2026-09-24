"""Serve the Hive Entrance: loopback and remote listeners guarding the Landing Board.

The Hive Entrance is the Hive's one HTTP door. Two listeners, loopback (always on) and remote (only
when explicitly exposed), serve the Landing Board (the versioned public API contract), device
enrolment, auth, push delivery, exposure control and the human inbox. Approval routes never exist on
the remote listener. Every client, the Hive Stand's (the machine the Queen, the orchestrator, runs
on) own console included, is a device enrolled with its own key and approved at the Hive Stand, and
logs in with that key plus the operator's password (ADR-0033). Phase 10's steps so far land what
this face re-exports: the credential primitives and the ceremony challenge book (``auth``), device
enrolment end to end (``enrol``: the enrolled-device model and state machine, the console
bootstrap, invites, redemption by Ed25519 key or passkey, the operator's decisions and the expiry
sweep), login, sessions bound to a key with every request signed, step-up, pending
confirmations, lockout, rate limits and the travel lock (``auth``'s sub-packages, roadmap 10.5e),
the Entrance Reducer (``reducer``), the Entrance tables (``store``) and the error tree
(``errors``). The push channels (``push``, roadmap 10.5b) and remote exposure (``expose``, 10.5a:
the mode check, the Hive's own certificate authority for mutual TLS, the tunnel child, the
loopback listener's Host check) have their own faces. The application itself (step 10.5) is
reached through its own modules, so importing this face never loads the web stack: ``app`` (both
listeners' applications from one route table), ``landing_board`` (the OpenAPI document),
``runtime`` (``build_entrance`` and the running ``HiveEntrance``), and the ``gate``, ``models``,
``routes``, ``streams`` and ``notify`` packages behind them.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Called by an enrolled device or the Observation
    Hive front end, over HTTP. Calls into queen (Layer 6) and below.

Key invariants:
    - This file holds re-exports and ``__all__`` only; the sub-packages' faces list every name.
    - Nothing in the Entrance stores a password, an invite code, a session token or a private
      key in the clear: the tables hold hashes and public keys, and the console's key is wrapped
      under the password.

See Also:
    - .claude/codingrules.md sections 8.15 and 15 for the Entrance's rules.
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md for enrolment,
      login and exposure.
    - hivemind.entrance.auth, hivemind.entrance.enrol and hivemind.entrance.store for the
      sub-packages behind this face; hivemind.entrance.push and hivemind.entrance.expose for the
      two with faces of their own.

Public API:
    - EntranceError and its subclasses: every refusal the Entrance makes on purpose.
    - KeyKind, PasswordHasher, RelyingParty, SoftPasskey, ChallengeBook: the credential
      primitives most callers need (the rest via hivemind.entrance.auth).
    - DeviceStatus, EnrolledDevice, DeviceDescription, DeviceInvite, OperatorCredential: the
      enrolled-device model and its state machine.
    - ConsoleDeps, bootstrap_operator, change_operator_password, unlock_console_key: the operator
      and the Hive Stand console.
    - EnrolmentDeps, EntranceIdentity, mint_invite, cancel_invite, passkey_options,
      redeem_ed25519, redeem_passkey, Ed25519Proof, ApprovalRequest, approve, deny, revoke, lock,
      unlock, LockReason, expire_due, steward_grant: device enrolment (the rest via
      hivemind.entrance.enrol).
    - EntranceStore, SqliteEntranceStore, MemoryEntranceStore, SplitSessionTable: the Entrance
      tables (the rest via hivemind.entrance.store).
    - SessionBook, SessionRules, AuthDeps, begin_login, finish_login, authenticate_request,
      authenticate_websocket, step_up, requires_step_up, hold, confirm: login, sessions, step-up
      and pending confirmations (the rest via hivemind.entrance.auth).
    - EntranceReducer, EntranceMode, ReduceReason, ReducerSeams, RemoteListenerControl,
      StreamCloser: the Entrance Reducer.
"""

from hivemind.entrance.auth import (
    AuthDeps,
    ChallengeBook,
    KeyKind,
    PasswordHasher,
    RelyingParty,
    SessionBook,
    SessionRules,
    SoftPasskey,
    authenticate_request,
    authenticate_websocket,
    begin_login,
    confirm,
    finish_login,
    hold,
    requires_step_up,
    step_up,
)
from hivemind.entrance.enrol import (
    ApprovalRequest,
    ConsoleDeps,
    DeviceDescription,
    DeviceInvite,
    DeviceStatus,
    Ed25519Proof,
    EnrolledDevice,
    EnrolmentDeps,
    EntranceIdentity,
    LockReason,
    OperatorCredential,
    approve,
    bootstrap_operator,
    cancel_invite,
    change_operator_password,
    deny,
    expire_due,
    lock,
    mint_invite,
    passkey_options,
    redeem_ed25519,
    redeem_passkey,
    revoke,
    steward_grant,
    unlock,
    unlock_console_key,
)
from hivemind.entrance.errors import (
    AuthenticationFailedError,
    BreakGlassRefusedError,
    CapabilityCeilingError,
    ChallengeRejectedError,
    ConfirmationRefusedError,
    ConsoleProtectedError,
    DeviceAlreadyExistsError,
    DeviceNotFoundError,
    DeviceStatusConflictError,
    EnrolmentRefusedError,
    EntranceError,
    EntranceModeConflictError,
    InvalidApprovalError,
    InvalidDeviceEntryError,
    InvalidDeviceTransitionError,
    InvalidModeTransitionError,
    InvalidPendingTransitionError,
    InviteAlreadyExistsError,
    InviteAlreadyUsedError,
    InviteExpiredError,
    InviteNotFoundError,
    KeyUnwrapError,
    OperatorAlreadyInitialisedError,
    OperatorNotInitialisedError,
    OperatorPasswordMismatchError,
    PasskeyRejectedError,
    PendingNotFoundError,
    PendingStatusConflictError,
    ReopenRefusedError,
    StepUpUnavailableError,
    StewardGrantError,
    TravelLockUnavailableError,
    WeakPasswordError,
)
from hivemind.entrance.reducer import (
    EntranceMode,
    EntranceReducer,
    ReduceReason,
    ReducerSeams,
    RemoteListenerControl,
    StreamCloser,
)
from hivemind.entrance.store import (
    EntranceStore,
    MemoryEntranceStore,
    SplitSessionTable,
    SqliteEntranceStore,
)

__all__ = [
    "ApprovalRequest",
    "AuthDeps",
    "AuthenticationFailedError",
    "BreakGlassRefusedError",
    "CapabilityCeilingError",
    "ChallengeBook",
    "ChallengeRejectedError",
    "ConfirmationRefusedError",
    "ConsoleDeps",
    "ConsoleProtectedError",
    "DeviceAlreadyExistsError",
    "DeviceDescription",
    "DeviceInvite",
    "DeviceNotFoundError",
    "DeviceStatus",
    "DeviceStatusConflictError",
    "Ed25519Proof",
    "EnrolledDevice",
    "EnrolmentDeps",
    "EnrolmentRefusedError",
    "EntranceError",
    "EntranceIdentity",
    "EntranceMode",
    "EntranceModeConflictError",
    "EntranceReducer",
    "EntranceStore",
    "InvalidApprovalError",
    "InvalidDeviceEntryError",
    "InvalidDeviceTransitionError",
    "InvalidModeTransitionError",
    "InvalidPendingTransitionError",
    "InviteAlreadyExistsError",
    "InviteAlreadyUsedError",
    "InviteExpiredError",
    "InviteNotFoundError",
    "KeyKind",
    "KeyUnwrapError",
    "LockReason",
    "MemoryEntranceStore",
    "OperatorAlreadyInitialisedError",
    "OperatorCredential",
    "OperatorNotInitialisedError",
    "OperatorPasswordMismatchError",
    "PasskeyRejectedError",
    "PasswordHasher",
    "PendingNotFoundError",
    "PendingStatusConflictError",
    "ReduceReason",
    "ReducerSeams",
    "RelyingParty",
    "RemoteListenerControl",
    "ReopenRefusedError",
    "SessionBook",
    "SessionRules",
    "SoftPasskey",
    "SplitSessionTable",
    "SqliteEntranceStore",
    "StepUpUnavailableError",
    "StewardGrantError",
    "StreamCloser",
    "TravelLockUnavailableError",
    "WeakPasswordError",
    "approve",
    "authenticate_request",
    "authenticate_websocket",
    "begin_login",
    "bootstrap_operator",
    "cancel_invite",
    "change_operator_password",
    "confirm",
    "deny",
    "expire_due",
    "finish_login",
    "hold",
    "lock",
    "mint_invite",
    "passkey_options",
    "redeem_ed25519",
    "redeem_passkey",
    "requires_step_up",
    "revoke",
    "step_up",
    "steward_grant",
    "unlock",
    "unlock_console_key",
]
