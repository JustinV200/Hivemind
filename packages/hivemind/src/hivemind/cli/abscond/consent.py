"""Check that a human, and only a human, asked for this Absconding: re-auth, policy, phrase.

Codingrules 15: "Absconding is human-only and break-glass: it requires password re-auth and an
explicit typed confirmation phrase, and it is never callable by Queen, Wardens, Workers,
autopilot, or tools." `reauthenticate` opens the Hive Stand console's key with the operator
password, the same proof a console login starts from (ADR-0041), and refuses when it does not open
or there is no console yet. `authorize_abscond` passes the Guard's `absconding` enforcement point
as the operator, whose role alone holds `abscond`; the human-only floor refuses every other
principal whatever it holds, and a refusal is a `guard.denied` row. `phrase_matches` compares the
typed phrase as every break-glass phrase is compared (NFKC, case-folded, single-spaced).
`Evidence` is what the trail records of the consent: how the human proved themselves, never the
password or the phrase.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.abscond`. Called by
    `.command`. Calls into `hivemind.common.secrets`, `hivemind.entrance.auth` (PasswordHasher,
    the break-glass phrases), `hivemind.entrance.enrol` (unlock_console_key), `hivemind.guard`,
    `hivemind.manifest` and pydantic only.

Key invariants:
    - The password lives only for the call that checks it; nothing here logs or stores it.
    - Nothing is torn down by this module; it only says yes or no.

See Also:
    - hivemind.guard.policy.floors.human_only for the floor behind the point.
    - hivemind.entrance.auth.step_up.phrases for the phrase.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import JsonValue, SecretStr

from hivemind.common.secrets import FileSecretStore
from hivemind.entrance.auth import PasswordHasher
from hivemind.entrance.auth.step_up import BREAK_GLASS_PHRASES, ActionKind, normalise_phrase
from hivemind.entrance.enrol.console import unlock_console_key
from hivemind.guard import (
    Capability,
    CapabilityFamily,
    EnforcementPoint,
    Enforcer,
    PolicyContext,
    PolicyRequest,
    PrincipalKind,
    PrincipalRef,
    role_set,
)
from hivemind.guard.policy import OPERATOR_ID
from hivemind.manifest import HiveManifest

OPERATOR_ROLE = "operator"  # The [roles.operator] table: the only role holding `abscond`.
REAUTH_METHOD = "operator_password"  # How the human proved themselves, for the trail.
PHRASE = BREAK_GLASS_PHRASES[ActionKind.ABSCOND]  # What the human types: "abscond my hive".

__all__ = [
    "PHRASE",
    "REAUTH_METHOD",
    "Evidence",
    "authorize_abscond",
    "phrase_matches",
    "reauthenticate",
]


@dataclass(frozen=True, slots=True)
class Evidence:
    """How the human confirmed this Absconding, as the trail records it (never a secret).

    Attributes:
        reason: The human's own reason.
        leavings_ticked: How many Leavings the human ticked for removal.
    """

    reason: str
    leavings_ticked: int

    def payload(self) -> dict[str, JsonValue]:
        """The `confirmation` field of `guard.absconded`: every check that passed, by name."""
        return {
            "reauth": REAUTH_METHOD,
            "phrase": "typed",
            "scope_review": "confirmed",
            "leavings_ticked": self.leavings_ticked,
        }


async def reauthenticate(manifest: HiveManifest, password: SecretStr) -> None:
    """Prove the operator password opens the Hive Stand console's key.

    Args:
        manifest: The loaded manifest; `[hive] secrets_dir` is read.
        password: The operator password, typed now.

    Raises:
        KeyUnwrapError: The password does not open the console key.
        OperatorNotInitialisedError: There is no console yet; set the operator password first.
    """
    secrets = FileSecretStore(manifest.resolve_path(manifest.hive.secrets_dir))
    # Latency: one secret-store read and one Argon2id derivation (about 0.1 s).
    await unlock_console_key(secrets, PasswordHasher(), password.get_secret_value())


async def authorize_abscond(enforcer: Enforcer) -> bool:
    """Pass the `absconding` point as the operator; a refusal is already on the trail.

    Args:
        enforcer: The Hive's Enforcer over its Guard policy and trail.

    Returns:
        True when the operator holds `abscond` and no floor refuses.
    """
    decision = await enforcer.check(_request(enforcer))
    return decision.allowed


def phrase_matches(typed: str) -> bool:
    """Return whether `typed` is the Absconding phrase, as every break-glass phrase is compared.

    Args:
        typed: What the human typed.

    Returns:
        True for "abscond my hive", whatever its case and spacing.
    """
    return normalise_phrase(typed) == normalise_phrase(PHRASE)


def _request(enforcer: Enforcer) -> PolicyRequest:
    """Build the `absconding` check: the human, needing `abscond`, holding the operator's set."""
    return PolicyRequest(
        principal=PrincipalRef(kind=PrincipalKind.OPERATOR, id=OPERATOR_ID, role=OPERATOR_ROLE),
        point=EnforcementPoint.ABSCONDING,
        needed=Capability(family=CapabilityFamily.ABSCOND),
        held=role_set(enforcer.policy, OPERATOR_ROLE),
        context=PolicyContext(),
    )
