"""Build valid hivemind.entrance records and events, and walk a stored device along its machine.

Every builder returns a real, validated model (codingrules 14.5) with sensible defaults for every
field a test does not care about. ``make_device(status=...)`` fills in whatever that status
requires (a key and a description once redeemed, ``approved_at`` once approved), so any
``DeviceStatus`` is valid on its own. ``redeem_changes`` and ``approve_changes`` are the
``DeviceChanges`` an INVITED-to-PENDING and a PENDING-to-APPROVED move need; ``changes_for`` picks
the right one for any edge, ``entry_event`` and ``edge_event`` the ``guard.entrance_*`` event each
store write carries, and ``walk_to`` drives a stored INVITED device along the state machine to any
status, so a contract test can start an edge from wherever it needs to. ``make_session`` and
``make_pending`` build the session and pending-confirmation records of roadmap step 10.5e.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used through the
    ``builders.entrance`` face by the tests under packages/hivemind/tests/unit/entrance and the
    Entrance store contract suites.

Key invariants:
    - Every builder that mints an id or a timestamp takes an optional ``clock`` (default a fresh
      FakeClock) so runs are deterministic.
    - ``walk_to`` moves a device only through ``EntranceStore.update_device_status``: it never
      writes a status the state machine did not reach.

See Also:
    - hivemind.entrance.enrol.models for the records built here.
    - hivemind.entrance.enrol.state for the paths ``walk_to`` follows.
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from hivemind.entrance.auth import (
    ActionKind,
    BindingKind,
    KeyKind,
    Listener,
    PendingConfirmation,
    PendingStatus,
    RelyingParty,
    Session,
    b64url_encode,
    sha256_hex,
)
from hivemind.entrance.auth.confirm import new_pending_id
from hivemind.entrance.enrol import (
    ENTRY_TRAIL_KINDS,
    DeviceDescription,
    DeviceInvite,
    DeviceStatus,
    EnrolledDevice,
    EntranceIdentity,
    can_transition,
    trail_kind,
)
from hivemind.entrance.store import DeviceChanges, EntranceStore
from hivemind.pheromone import GuardEvent
from waggle.clock import Clock, FakeClock
from waggle.ids import DeviceId, new_device_id, new_hive_id, new_node_id
from waggle.signing import Ed25519Signer

INVITE_TTL = timedelta(minutes=15)  # [entrance] invite_ttl_minutes' default.
PENDING_TTL = timedelta(hours=24)  # [entrance] pending_ttl_hours' default.
APPROVAL_TTL = timedelta(days=90)  # An approval expiry a test can reason about.
ORIGIN = "http://localhost:8710"  # The Hive Stand's loopback listener, a secure context.
RELYING_PARTY = RelyingParty(id="localhost", name="HiveMind", origins=(ORIGIN,))
ADDRESS = "100.64.0.7"  # A requesting address on the overlay.

# From INVITED, the statuses each target is reached through, in order (the state machine's own
# shortest paths): what walk_to replays through the store.
PATHS: dict[DeviceStatus, tuple[DeviceStatus, ...]] = {
    DeviceStatus.INVITED: (),
    DeviceStatus.PENDING: (DeviceStatus.PENDING,),
    DeviceStatus.APPROVED: (DeviceStatus.PENDING, DeviceStatus.APPROVED),
    DeviceStatus.LOCKED: (DeviceStatus.PENDING, DeviceStatus.APPROVED, DeviceStatus.LOCKED),
    DeviceStatus.DENIED: (DeviceStatus.PENDING, DeviceStatus.DENIED),
    DeviceStatus.EXPIRED: (DeviceStatus.EXPIRED,),
    DeviceStatus.REVOKED: (DeviceStatus.REVOKED,),
}


# ──────────────────────────────────────────────────────────────────────────────
# Records, events and store walks
# ──────────────────────────────────────────────────────────────────────────────


def make_identity(clock: Clock | None = None, actor: str = "system") -> EntranceIdentity:
    """Build an EntranceIdentity with a fresh Hive and node id."""
    active_clock = clock if clock is not None else FakeClock()
    return EntranceIdentity(new_hive_id(active_clock), new_node_id(active_clock), actor)


# One node for every event the store-level builders make, so events recorded at the same instant
# keep their recording order on the trail (it orders by time, then node).
STORE_IDENTITY = make_identity()


def entry_event(device: EnrolledDevice, clock: Clock | None = None) -> GuardEvent:
    """Build the entry event ``put_device`` records with ``device``."""
    active_clock = clock if clock is not None else FakeClock()
    kind = ENTRY_TRAIL_KINDS.get(device.status, "guard.entrance_invited")
    return STORE_IDENTITY.event(active_clock, kind, device.id, {})


def edge_event(
    device: EnrolledDevice, from_status: DeviceStatus, to_status: DeviceStatus, clock: Clock
) -> GuardEvent:
    """Build the event an edge records; a forbidden edge gets an arbitrary guard kind."""
    allowed = can_transition(from_status, to_status)
    kind = trail_kind(from_status, to_status) if allowed else "guard.entrance_revoked"
    return STORE_IDENTITY.event(clock, kind, device.id, {})


def ed25519_public_key(signer: Ed25519Signer | None = None) -> str:
    """Return a device public key as the Entrance stores it: unpadded base64url of 32 bytes."""
    key = signer if signer is not None else Ed25519Signer.generate()
    return b64url_encode(key.public_key_bytes)


def make_description(**overrides: object) -> DeviceDescription:
    """Build a DeviceDescription for a phone, with any field overridden."""
    fields: dict[str, object] = {
        "name": "Pixel 9",
        "platform": "Android 16",
        "user_agent": "Mozilla/5.0 (Linux; Android 16)",
    }
    fields.update(overrides)
    return DeviceDescription.model_validate(fields)


def redeem_changes(clock: Clock | None = None, public_key: str | None = None) -> DeviceChanges:
    """Return what INVITED to PENDING sets: an Ed25519 key, a description, the request's expiry."""
    active_clock = clock if clock is not None else FakeClock()
    return {
        "key_kind": KeyKind.ED25519,
        "public_key": public_key if public_key is not None else ed25519_public_key(),
        "description": make_description(),
        "expires_at": active_clock.now() + PENDING_TTL,
    }


def approve_changes(clock: Clock | None = None) -> DeviceChanges:
    """Return what PENDING to APPROVED sets: name, capabilities, spend cap, expiry, approval."""
    active_clock = clock if clock is not None else FakeClock()
    return {
        "name": "phone",
        "capabilities": ("observe", "entrance:submit", "entrance:answer"),
        "spend_cap_usd_per_day": 5.0,
        "expires_at": active_clock.now() + APPROVAL_TTL,
        "interactive": True,
        "approved_at": active_clock.now(),
    }


def changes_for(
    from_status: DeviceStatus, to_status: DeviceStatus, clock: Clock | None = None
) -> DeviceChanges:
    """Return the DeviceChanges the edge ``from_status`` to ``to_status`` needs (often none)."""
    if to_status is DeviceStatus.PENDING:
        return redeem_changes(clock)
    if from_status is DeviceStatus.PENDING and to_status is DeviceStatus.APPROVED:
        return approve_changes(clock)
    return {}


def make_device(
    clock: Clock | None = None, status: DeviceStatus = DeviceStatus.INVITED, **overrides: object
) -> EnrolledDevice:
    """Build an EnrolledDevice valid in ``status``, with any field overridden."""
    active_clock = clock if clock is not None else FakeClock()
    fields: dict[str, object] = {
        "id": new_device_id(active_clock),
        "name": "phone",
        "status": status,
        "created_at": active_clock.now(),
        "expires_at": active_clock.now() + INVITE_TTL,
    }
    # Everything past INVITED that went through PENDING carries its key and description.
    if status in (DeviceStatus.PENDING, DeviceStatus.APPROVED, DeviceStatus.LOCKED):
        fields.update(redeem_changes(active_clock))
    if status is DeviceStatus.DENIED:
        fields.update(redeem_changes(active_clock))
    if status in (DeviceStatus.APPROVED, DeviceStatus.LOCKED):
        fields.update(approve_changes(active_clock))
    fields.update(overrides)
    return EnrolledDevice.model_validate(fields)


def make_invite(
    device: EnrolledDevice, clock: Clock | None = None, **overrides: object
) -> DeviceInvite:
    """Build an unused DeviceInvite for ``device``, its code hashed from a fresh random code."""
    active_clock = clock if clock is not None else FakeClock()
    fields: dict[str, object] = {
        "code_hash": sha256_hex(secrets.token_bytes(16)),
        "device_id": device.id,
        "label": device.name,
        "created_at": active_clock.now(),
        "expires_at": active_clock.now() + INVITE_TTL,
    }
    fields.update(overrides)
    return DeviceInvite.model_validate(fields)


async def walk_to(
    store: EntranceStore, device: EnrolledDevice, target: DeviceStatus, clock: Clock | None = None
) -> EnrolledDevice:
    """Move a stored INVITED ``device`` along ``PATHS[target]`` through the store's status change.

    Returns:
        The device as stored once it reaches ``target``.
    """
    active_clock = clock if clock is not None else FakeClock()
    current = device
    for next_status in PATHS[target]:
        changes = changes_for(current.status, next_status, active_clock)
        event = edge_event(current, current.status, next_status, active_clock)
        current = await store.update_device_status(
            current.id, current.status, next_status, event, **changes
        )
    return current


def make_session(device_id: DeviceId, clock: Clock | None = None, **overrides: object) -> Session:
    """Build an open Session for ``device_id``: an Ed25519-bound remote session, 12 hours long."""
    active_clock = clock if clock is not None else FakeClock()
    fields: dict[str, object] = {
        "token_hash": sha256_hex(secrets.token_bytes(32)),
        "device_id": device_id,
        "binding_kind": BindingKind.ED25519,
        "binding_key": ed25519_public_key(),
        "listener": Listener.REMOTE,
        "address": "100.64.0.7",
        "network": "100.64.0.0/24",
        "created_at": active_clock.now(),
        "last_seen_at": active_clock.now(),
        "expires_at": active_clock.now() + timedelta(hours=12),
    }
    fields.update(overrides)
    return Session.model_validate(fields)


def make_pending(
    device_id: DeviceId, clock: Clock | None = None, **overrides: object
) -> PendingConfirmation:
    """Build a PENDING confirmation of a goal for ``device_id``, held for an hour."""
    active_clock = clock if clock is not None else FakeClock()
    fields: dict[str, object] = {
        "id": new_pending_id(active_clock),
        "device_id": device_id,
        "action": ActionKind.GOAL,
        "payload": {"budget_usd": 12.5, "goal": "tidy the garden"},
        "status": PendingStatus.PENDING,
        "created_at": active_clock.now(),
        "expires_at": active_clock.now() + timedelta(hours=1),
    }
    fields.update(overrides)
    return PendingConfirmation.model_validate(fields)
