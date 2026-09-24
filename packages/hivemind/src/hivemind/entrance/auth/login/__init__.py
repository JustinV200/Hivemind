"""Hold login: a device's key proof first, then the operator's password, then a bound session.

Login at the Hive Entrance (the Hive's one HTTP door) is two factors, the device's own key and the
operator's password, the key proof first (ADR-0033). ``deps`` bundles what it needs (``AuthDeps``,
shared with step-up and the confirmations); ``factors`` checks the device proof (an Ed25519
signature over ``login_string``, or a WebAuthn assertion) and the password; ``refusals`` charges a
failure to the right party (an invalid proof to its address, a wrong password to its device, with a
lockout at ``lockout_attempts``) and records it; ``flow`` is ``begin_login`` and ``finish_login``.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.auth``. Called by the
    login routes (a later step). Calls into the credential primitives, the session book, the
    travel lock, the Entrance tables and ``hivemind.entrance.enrol.standing.lock``.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - An invalid proof never reaches the password check and never counts against its device.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md, "Login is the device
      key plus the password, the key proof first".

Public API:
    - AuthDeps, LoginCeremony, LoginGuards, LOGIN_CHALLENGE_TTL: the dependencies (deps).
    - DeviceProof, VerifiedProof, verify_proof, binding_fits, password_holds: the factors
      (factors).
    - refuse, refuse_proof, refuse_password, LOCKOUT_ACTOR: refusals and lockout (refusals).
    - begin_login, finish_login, login_refusal, passkey_request_options, LoginChallenge: the
      flow (flow).
"""

from hivemind.entrance.auth.login.deps import (
    LOGIN_CHALLENGE_TTL,
    AuthDeps,
    LoginCeremony,
    LoginGuards,
)
from hivemind.entrance.auth.login.factors import (
    DeviceProof,
    VerifiedProof,
    binding_fits,
    password_holds,
    verify_proof,
)
from hivemind.entrance.auth.login.flow import (
    LoginChallenge,
    begin_login,
    finish_login,
    login_refusal,
    passkey_request_options,
)
from hivemind.entrance.auth.login.refusals import (
    LOCKOUT_ACTOR,
    refuse,
    refuse_password,
    refuse_proof,
)

__all__ = [
    "LOCKOUT_ACTOR",
    "LOGIN_CHALLENGE_TTL",
    "AuthDeps",
    "DeviceProof",
    "LoginCeremony",
    "LoginChallenge",
    "LoginGuards",
    "VerifiedProof",
    "begin_login",
    "binding_fits",
    "finish_login",
    "login_refusal",
    "passkey_request_options",
    "password_holds",
    "refuse",
    "refuse_password",
    "refuse_proof",
    "verify_proof",
]
