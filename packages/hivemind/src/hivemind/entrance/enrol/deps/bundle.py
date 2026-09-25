"""Define EnrolmentDeps: everything the enrolment flows are built from, in four small bundles.

Device enrolment at the Hive Entrance (the Hive's one HTTP door; ADR-0041) touches a lot: the
Entrance tables and the Pheromone Trail (audit log) it records on, the Guard policy whose
``device`` role bounds every approval, the ``[entrance]`` lifetimes of an invite and of a waiting
request, the base URL an invite link starts with, the Hive's own public key (returned to a
redeeming program so it pins it, ADR-0042), the WebAuthn relying party and the challenge book a
passkey is registered against, three seams later steps implement (who is told, who is cut off,
whose goals are cancelled), and the certifier that issues a device's mutual-TLS certificate at
approval. A constructor stays within five parameters (codingrules 5.1), so these
are four bundles grouped by what they are for, each frozen, each checked where a value could be
wrong, and one ``EnrolmentDeps`` holding them. The composition root builds it once.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.enrol.deps``. Built by
    the Entrance's composition root (``entrance/app.py``, a later step), a ``hive entrance``
    command or a test; passed to every function in ``hivemind.entrance.enrol``'s flows. Calls into
    ``hivemind.entrance.auth`` (the relying party, the challenge book), ``hivemind.guard`` (the
    policy), ``hivemind.pheromone`` (the trail) and this package's seams.

Key invariants:
    - Every bundle is frozen; ``EnrolmentRules`` refuses a non-positive lifetime or an invite base
      URL that is not a plain http(s) origin and path, and ``EnrolmentCeremony`` a Hive key that
      is not a raw 32-byte Ed25519 public key.
    - ``EnrolmentRecords.trail`` is the trail the store itself writes its events to, so every
      ``guard.entrance_*`` event of this Hive lands on one trail.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md for enrolment.
    - hivemind.entrance.enrol.console for ConsoleDeps, the operator bootstrap's own bundle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from hivemind.entrance.auth.challenges import ChallengeBook
from hivemind.entrance.auth.keys import ED25519_PUBLIC_KEY_BYTES
from hivemind.entrance.auth.passkeys import RelyingParty
from hivemind.entrance.enrol.certificates import DeviceCertifier
from hivemind.entrance.enrol.deps.goals import GoalLedger, NullGoalLedger
from hivemind.entrance.enrol.deps.identity import EntranceIdentity
from hivemind.entrance.enrol.deps.notifier import NullSecurityNotifier, SecurityNotifier
from hivemind.entrance.enrol.deps.offboarder import DeviceOffboarder, NullDeviceOffboarder
from hivemind.guard import GuardPolicy
from hivemind.pheromone import PheromoneTrail
from waggle.clock import Clock

if TYPE_CHECKING:
    # Type-only: hivemind.entrance.store imports this package's models, so a runtime import here
    # would be a cycle; enrolment reaches the tables only through the protocol's methods.
    from hivemind.entrance.store.protocol import EntranceStore

# The schemes an invite link may start with: https for a remote Entrance, http only for the Hive
# Stand's own loopback listener, which browsers already treat as a secure context.
_INVITE_SCHEMES = frozenset({"http", "https"})

__all__ = [
    "EnrolmentCeremony",
    "EnrolmentDeps",
    "EnrolmentRecords",
    "EnrolmentRules",
    "EnrolmentSeams",
]


@dataclass(frozen=True, slots=True)
class EnrolmentRecords:
    """Where enrolment keeps and audits its records, the clock stamping both, and who it is.

    Attributes:
        store: The Entrance tables; every status change goes through them with its event.
        trail: The Pheromone Trail the store writes to; enrolment records only what changes no
            state here (a refused redemption, ``guard.entrance_redeem_failed``).
        clock: Stamps every record, expiry and event.
        identity: The Hive, node and default actor every event carries.
    """

    store: EntranceStore
    trail: PheromoneTrail
    clock: Clock
    identity: EntranceIdentity


@dataclass(frozen=True, slots=True)
class EnrolmentRules:
    """What the Hive Manifest decides about enrolment.

    Attributes:
        policy: The Guard policy; its ``device`` role's ``allow`` list is the ceiling no approval
            exceeds, and its ``proposed`` list what an approval grants when it names nothing.
        invite_ttl: How long an unredeemed invite admits (``[entrance] invite_ttl_minutes``).
        pending_ttl: How long a redeemed, undecided request waits (``pending_ttl_hours``).
        invite_base_url: What an invite link starts with: ``public_url`` for a remote Entrance,
            or the loopback listener's ``http://localhost:<port>``.
    """

    policy: GuardPolicy
    invite_ttl: timedelta
    pending_ttl: timedelta
    invite_base_url: str

    def __post_init__(self) -> None:
        """Refuse a non-positive lifetime, or a base URL that is not a plain http(s) origin."""
        if self.invite_ttl <= timedelta(0) or self.pending_ttl <= timedelta(0):
            raise ValueError("Invite and pending lifetimes must both be positive.")
        # The link's fragment carries the code, so the base must not bring its own query or
        # fragment, and it must be a web origin a browser or program can open.
        parts = urlsplit(self.invite_base_url)
        if parts.scheme not in _INVITE_SCHEMES or not parts.netloc or parts.query or parts.fragment:
            raise ValueError(
                f"The invite base URL {self.invite_base_url!r} must be an http(s) origin and "
                "path, with no query or fragment."
            )


@dataclass(frozen=True, slots=True)
class EnrolmentCeremony:
    """What a redeeming device's proof is checked against, and what it is handed back.

    Attributes:
        hive_public_key: The Hive's own Ed25519 public key, raw; returned to every redeeming
            device so a program pins it and can verify the Hive's webhooks (ADR-0042).
        relying_party: The WebAuthn relying party a browser's passkey is registered for.
        challenges: Where each passkey registration's challenge waits, bound to its invite.
    """

    hive_public_key: bytes
    relying_party: RelyingParty
    challenges: ChallengeBook

    def __post_init__(self) -> None:
        """Refuse a Hive key that is not a raw Ed25519 public key."""
        if len(self.hive_public_key) != ED25519_PUBLIC_KEY_BYTES:
            raise ValueError(
                f"The Hive's public key must be {ED25519_PUBLIC_KEY_BYTES} raw bytes, not "
                f"{len(self.hive_public_key)}."
            )


@dataclass(frozen=True, slots=True)
class EnrolmentSeams:
    """The collaborators later steps provide; each defaults to its documented no-op.

    Attributes:
        notifier: Tells every other approved device that a security event happened (10.5b).
        offboarder: Ends a device's sessions and push subscriptions when it leaves APPROVED
            (10.5e and 10.5b).
        goals: Lists and cancels a device's open goals at revocation (the Queen's goal table).
        certifier: Issues a device's client certificate at approval; the default has no
            authority (a loopback-only Hive) and issues nothing.
    """

    notifier: SecurityNotifier = field(default_factory=NullSecurityNotifier)
    offboarder: DeviceOffboarder = field(default_factory=NullDeviceOffboarder)
    goals: GoalLedger = field(default_factory=NullGoalLedger)
    certifier: DeviceCertifier = field(default_factory=DeviceCertifier)


@dataclass(frozen=True, slots=True)
class EnrolmentDeps:
    """Everything every enrolment flow needs, built once by the composition root.

    Attributes:
        records: The tables, the trail, the clock and the identity events carry.
        rules: The Guard policy, the lifetimes and the invite base URL.
        ceremony: The Hive's key, the relying party and the challenge book.
        seams: The notifier, the offboarder and the goal ledger.
    """

    records: EnrolmentRecords
    rules: EnrolmentRules
    ceremony: EnrolmentCeremony
    seams: EnrolmentSeams = field(default_factory=EnrolmentSeams)
