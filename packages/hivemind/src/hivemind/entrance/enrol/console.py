"""Keep the operator's password and the Hive Stand console: bootstrap, change, reset, unlock.

The Hive Stand (the machine the Queen, the orchestrator, runs on) has its own console, and it is a
device like any other (ADR-0033): it holds an Ed25519 key, logs in with that key plus the operator
password, and is recorded APPROVED and **loopback-bound**, so its sessions open only on the loopback
listener. That is how the first remote device ever gets approved without a special path.
``bootstrap_operator`` sets it all up once: it checks the password's strength before writing
anything, mints the console key and stores it in the secret store **wrapped** under the password
(``hivemind.entrance.auth.wrap``: bees, the Hive's agent processes, share the Hive Stand's
operating-system user, so a key kept in the clear would be theirs too), records the console device,
and writes the password hash **last**, because that row is what marks the operator as initialised:
an interrupted bootstrap is simply run again, reusing a console key it can open and replacing a
record that key does not match. The console's entry is recorded on the Pheromone Trail (the Hive's
audit log) as ``guard.entrance_approved`` and a replaced record's revocation as
``guard.entrance_revoked``, each in the same step as the record itself (codingrules Appendix C).
``change_operator_password`` proves the current password and re-wraps the console key under the
new one in the same operation; ``unlock_console_key`` opens it for a console login, and
``console_record`` finds the console's record for the key it opened. Two operations run only while
``hive serve`` is stopped (the caller holds the Hive's serve lock), because they change what a
running Entrance holds in memory: ``reset_operator`` is ``--reset`` for a lost password (every
device leaves, its sessions end, a new console key is minted under the new password and recorded
as a new console, and the hash is written last), and ``unlock_console`` unlocks a console locked
out by wrong passwords, which could otherwise never log in to unlock itself (ADR-0033). Console
sessions are never persisted by anything: the console's session lives in the memory of the process
that opened it, and the unwrapped key only as long as that session does.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.enrol``. Called by
    ``hive entrance operator password`` (and later ``hive init``, roadmap 14.2), ``hive entrance
    unlock --console`` and the console's login. Calls into ``hivemind.entrance.auth`` (password
    hashing, key wrapping, the session table's end reasons),
    ``hivemind.common.secrets``, ``hivemind.entrance.enrol.deps`` (the identity events carry) and
    the Entrance tables through ``EntranceStore``.

Key invariants:
    - The password hash is written last, by a bootstrap and by a reset alike; its presence means
      a bootstrap completed, so a second bootstrap raises ``OperatorAlreadyInitialisedError`` and
      writes nothing, and an interrupted reset is simply run again.
    - A reset leaves no device admitted but the new console, and every change it makes is a
      state-machine edge with its ``guard.entrance_*`` event.
    - The console's private key is never stored in the clear and never leaves this module except
      as the signer ``unlock_console_key`` returns.
    - The console is the operator at the Hive Stand's keyboard, so it holds every capability the
      operator uses and **no spend cap**: a cap would only stop the operator spending their own
      money from their own machine, while caps exist to bound a remote program's blast radius;
      step-up still applies above ``step_up_spend`` (codingrules 8.15).

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md for the console
      device and the wrapped key.
    - hivemind.entrance.auth.wrap for the blob format.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from pydantic import JsonValue

from hivemind.common.logging import get_logger
from hivemind.common.secrets import SecretStore
from hivemind.entrance.auth.canonical import b64url_encode
from hivemind.entrance.auth.keys import KeyKind
from hivemind.entrance.auth.password import PasswordHasher, check_password_strength
from hivemind.entrance.auth.session.models import EndReason
from hivemind.entrance.auth.wrap import unwrap_private_key, wrap_private_key
from hivemind.entrance.enrol.deps import EntranceIdentity
from hivemind.entrance.enrol.models import DeviceDescription, EnrolledDevice
from hivemind.entrance.enrol.state import ENTRY_TRAIL_KINDS, DeviceStatus, trail_kind
from hivemind.entrance.errors import (
    DeviceStatusConflictError,
    KeyUnwrapError,
    OperatorAlreadyInitialisedError,
    OperatorNotInitialisedError,
    OperatorPasswordMismatchError,
)
from hivemind.pheromone import GuardEvent
from waggle.clock import Clock
from waggle.ids import new_device_id
from waggle.signing import Ed25519Signer

if TYPE_CHECKING:
    # Type-only: hivemind.entrance.store imports this package's models, so a runtime import here
    # would be a cycle; this module only calls the store through its protocol's methods.
    from hivemind.entrance.store.protocol import EntranceStore

CONSOLE_KEY_NAME = "console.ed25519"  # The secret store name of the console's wrapped key.
CONSOLE_DEVICE_NAME = "Hive Stand console"  # How the console appears in every device list.
CONSOLE_USER_AGENT = "hivemind"  # The console is the Hive's own code, not a browser.
CONSOLE_REPLACED = "console_replaced"  # Why a stale console record is revoked, on the trail.
OPERATOR_RESET = "operator_reset"  # Why a reset moves every device out, on the trail.
# Where a reset moves each device that is not already terminal: every one leaves, the console
# included, because its key is sealed under the lost password (ADR-0033). A request still waiting
# is denied (PENDING has no edge to REVOKED); every other standing is revoked.
_RESET_EDGES: Mapping[DeviceStatus, DeviceStatus] = MappingProxyType(
    {
        DeviceStatus.INVITED: DeviceStatus.REVOKED,
        DeviceStatus.PENDING: DeviceStatus.DENIED,
        DeviceStatus.APPROVED: DeviceStatus.REVOKED,
        DeviceStatus.LOCKED: DeviceStatus.REVOKED,
    }
)
# The standings a live console record can hold: approved, or locked out until unlocked.
_LIVE_CONSOLE = frozenset({DeviceStatus.APPROVED, DeviceStatus.LOCKED})
# Everything the operator does at the Hive Stand, as capability strings (hivemind.guard's grammar,
# not imported here): submit and answer, push, steward, every observation, both Cell kinds, every
# Comb Shield tier and any spend, and the operator's own C2 content (the chat, the inbox, a held
# request's payload), without which the console could not see what it answers or confirms.
CONSOLE_CAPABILITIES = (
    "cell:comb_shield:*",
    "cell:hive_stand",
    "cell:virtual",
    "entrance:answer",
    "entrance:push",
    "entrance:steward",
    "entrance:submit",
    "honey:clearance:c2",
    "observe",
    "observe:honey:*",
    "observe:thoughts",
    "spend:*",
)

log = get_logger(__name__)

__all__ = [
    "CONSOLE_CAPABILITIES",
    "CONSOLE_DEVICE_NAME",
    "CONSOLE_KEY_NAME",
    "CONSOLE_REPLACED",
    "OPERATOR_RESET",
    "ConsoleDeps",
    "bootstrap_operator",
    "change_operator_password",
    "console_record",
    "reset_operator",
    "unlock_console",
    "unlock_console_key",
]


@dataclass(frozen=True, slots=True)
class ConsoleDeps:
    """What the operator and console operations need, built by the composition root.

    Attributes:
        store: The Entrance tables.
        secrets: The Hive's secret store (``[hive] secrets_dir``), where the wrapped key lives.
        hasher: The Entrance's one PasswordHasher, whose semaphore bounds every derivation.
        clock: Stamps the operator row, the console record and their trail events.
        identity: The Hive, node and actor the console's trail events carry (``"human"`` when
            ``hive entrance operator password`` runs at the Hive Stand).
    """

    store: EntranceStore
    secrets: SecretStore
    hasher: PasswordHasher
    clock: Clock
    identity: EntranceIdentity


async def bootstrap_operator(deps: ConsoleDeps, password: str) -> EnrolledDevice:
    """Set the operator password and record the Hive Stand console, once per Hive.

    Args:
        deps: The Entrance tables, secret store, hasher and clock.
        password: The operator password, typed at the Hive Stand.

    Returns:
        The console's record: APPROVED, loopback-bound, interactive, Ed25519, uncapped.

    Raises:
        OperatorAlreadyInitialisedError: The operator password is already set; changing it is
            ``change_operator_password``.
        WeakPasswordError: The password breaks the length rule; nothing was written.
    """
    # Latency: one local read of the Entrance tables (SQLite's own busy timeout bounds it); a
    # row here means an earlier bootstrap finished, since the password is written last.
    if await deps.store.get_operator() is not None:
        raise OperatorAlreadyInitialisedError(
            "The operator is already initialised; change the password with "
            "change_operator_password (hive entrance operator password)."
        )
    # Checked before the first write, so a weak password never leaves a half-made console.
    check_password_strength(password)
    # Latency: one or two Argon2id derivations (about 0.1 s each, in worker threads) and a few
    # local writes; none of it touches the network.
    signer = await _console_signer(deps, password)
    device = await _record_console(deps, signer)
    # Last on purpose: this row is what says "initialised" (module docstring).
    await deps.store.set_operator_password_hash(await deps.hasher.hash(password), deps.clock.now())
    return device


async def change_operator_password(deps: ConsoleDeps, old: str, new: str) -> None:
    """Change the operator password and re-wrap the console key under it, in one operation.

    The key is re-wrapped before the hash changes. If the process dies between the two, running
    the same change again finishes it: the key already opens with ``new``, so only the hash is
    left to write.

    Args:
        deps: The Entrance tables, secret store, hasher and clock.
        old: The current password.
        new: The new password.

    Raises:
        OperatorNotInitialisedError: No operator yet, or the console key is missing.
        OperatorPasswordMismatchError: ``old`` is not the current password.
        WeakPasswordError: ``new`` breaks the length rule; nothing was written.
        KeyUnwrapError: The console key opens with neither password (it was damaged).
    """
    # Latency: one local read, then one Argon2id verification (about 0.1 s in a worker thread).
    operator = await deps.store.get_operator()
    if operator is None:
        raise OperatorNotInitialisedError("The operator has no password to change yet.")
    if not await deps.hasher.verify(old, operator.password_hash):
        raise OperatorPasswordMismatchError("The current operator password is not correct.")
    check_password_strength(new)
    # Latency: one small secret-store read, then two or three derivations to re-wrap and hash.
    blob = await _require_console_blob(deps.secrets)
    try:
        private_key = await unwrap_private_key(deps.hasher, old, CONSOLE_KEY_NAME, blob)
    except KeyUnwrapError:
        # An earlier run of this same change re-wrapped the key and died before the hash: the
        # key already opens with `new`, which proves it; anything else propagates as damaged.
        await unwrap_private_key(deps.hasher, new, CONSOLE_KEY_NAME, blob)
    else:
        rewrapped = await wrap_private_key(deps.hasher, new, CONSOLE_KEY_NAME, private_key)
        await deps.secrets.put(CONSOLE_KEY_NAME, rewrapped)
    await deps.store.set_operator_password_hash(await deps.hasher.hash(new), deps.clock.now())


async def unlock_console_key(
    secrets: SecretStore, hasher: PasswordHasher, password: str
) -> Ed25519Signer:
    """Open the console's wrapped key with the operator password, for a console login.

    Args:
        secrets: The Hive's secret store.
        hasher: The Entrance's PasswordHasher.
        password: The operator password, typed at the Hive Stand.

    Returns:
        The console's signer; keep it only for the life of the session it opens.

    Raises:
        OperatorNotInitialisedError: There is no console key yet (no bootstrap).
        KeyUnwrapError: The password does not open it; never says more than that.
    """
    # Latency: one small secret-store read and one Argon2id derivation (about 0.1 s).
    blob = await _require_console_blob(secrets)
    return Ed25519Signer(await unwrap_private_key(hasher, password, CONSOLE_KEY_NAME, blob))


async def console_record(store: EntranceStore, signer: Ed25519Signer) -> EnrolledDevice:
    """Find the live console record that holds ``signer``'s key: the device a console logs in as.

    Args:
        store: The Entrance tables.
        signer: The console key ``unlock_console_key`` opened.

    Returns:
        The console's record, APPROVED or LOCKED.

    Raises:
        OperatorNotInitialisedError: No live console holds this key (a reset left none, or the
            record was revoked); only ``hive entrance operator password --reset`` makes one.
    """
    public_key = b64url_encode(signer.public_key_bytes)
    # Latency: one local read of the device table, a handful of rows.
    for device in await store.list_devices():
        # The console is the loopback-bound record holding this very key, still admitted.
        held = device.loopback_bound and device.public_key == public_key
        if held and device.status in _LIVE_CONSOLE:
            return device
    raise OperatorNotInitialisedError(
        "No live console record holds the console key; reset the operator password with "
        "hive entrance operator password --reset while hive serve is stopped."
    )


async def reset_operator(deps: ConsoleDeps, new_password: str) -> EnrolledDevice:
    """Replace a lost operator password: every device leaves, and a new console is minted.

    Run only while ``hive serve`` is stopped (the caller holds the serve lock). Every device that
    is not already terminal leaves (a waiting request is denied, every other standing revoked),
    its persisted sessions end, the old console key is deleted, a new one is minted and wrapped
    under ``new_password`` and recorded as the new console, and the new hash is written last.

    Args:
        deps: The Entrance tables, secret store, hasher and clock.
        new_password: The new operator password.

    Returns:
        The new console's record.

    Raises:
        OperatorNotInitialisedError: There is no operator to reset; set the password instead.
        WeakPasswordError: ``new_password`` breaks the length rule; nothing was written.
    """
    # Latency: one local read; a reset of a Hive that never had an operator is a mistake.
    if await deps.store.get_operator() is None:
        raise OperatorNotInitialisedError("There is no operator password to reset; set one.")
    check_password_strength(new_password)
    await _dismiss_every_device(deps)
    # Sealed under the lost password, the old key is useless: remove it so a new one is minted.
    await deps.secrets.delete(CONSOLE_KEY_NAME)
    # Latency: two Argon2id derivations (wrap, hash) and a few local writes.
    console = await _record_console(deps, await _console_signer(deps, new_password))
    await deps.store.set_operator_password_hash(
        await deps.hasher.hash(new_password), deps.clock.now()
    )
    return console


async def unlock_console(deps: ConsoleDeps, password: str) -> EnrolledDevice:
    """Unlock the Hive Stand console offline, proving the password by opening its key.

    A locked console cannot log in, so the loopback unlock route is out of its reach; this is
    ``hive entrance unlock --console``, run only while ``hive serve`` is stopped.

    Args:
        deps: The Entrance tables, secret store, hasher and clock.
        password: The operator password, typed at the Hive Stand.

    Returns:
        The console's record, APPROVED again.

    Raises:
        OperatorNotInitialisedError: There is no console key, or no live console holds it.
        KeyUnwrapError: The password does not open the console key.
        DeviceStatusConflictError: The console is not locked.
    """
    # Latency: one Argon2id derivation, then one local read.
    console = await console_record(
        deps.store, await unlock_console_key(deps.secrets, deps.hasher, password)
    )
    if console.status is not DeviceStatus.LOCKED:
        raise DeviceStatusConflictError(console.id, DeviceStatus.LOCKED, console.status)
    kind = trail_kind(DeviceStatus.LOCKED, DeviceStatus.APPROVED)
    event = deps.identity.event(deps.clock, kind, console.id, {"offline": True})
    # Latency: one local transaction writing the row and its event together.
    return await deps.store.update_device_status(
        console.id, DeviceStatus.LOCKED, DeviceStatus.APPROVED, event
    )


async def _dismiss_every_device(deps: ConsoleDeps) -> None:
    """Move every device that is not terminal out along its reset edge, ending its sessions."""
    # One device at a time, each edge with its own event in its own step; a rerun skips the
    # devices an interrupted reset already moved (they are terminal now).
    for device in await deps.store.list_devices():
        target = _RESET_EDGES.get(device.status)
        if target is None:
            continue
        kind = trail_kind(device.status, target)
        event = deps.identity.event(deps.clock, kind, device.id, {"reason": OPERATOR_RESET})
        await deps.store.update_device_status(device.id, device.status, target, event)
        # Console sessions lived only in the stopped process; any other device's persisted ones
        # end here too, so nothing waits for the next start's re-validation.
        await deps.store.sessions.end_for_device(device.id, deps.clock.now(), EndReason.REVOKED)


async def _console_signer(deps: ConsoleDeps, password: str) -> Ed25519Signer:
    """Reuse a console key an interrupted bootstrap left (if it opens), or mint and wrap one."""
    blob = await deps.secrets.get(CONSOLE_KEY_NAME)
    # A blob already here means an interrupted bootstrap: keep its key if this password opens it.
    if blob is not None:
        try:
            return Ed25519Signer(
                await unwrap_private_key(deps.hasher, password, CONSOLE_KEY_NAME, blob)
            )
        except KeyUnwrapError:
            # Sealed under another password by an interrupted attempt; no password was ever set,
            # so nothing can have logged in with that key and it is safe to replace.
            log.debug("entrance.console_key_replaced", secret=CONSOLE_KEY_NAME)
    # First bootstrap (or an unopenable leftover): mint from the CSPRNG and store it only wrapped.
    signer = Ed25519Signer.generate()
    wrapped = await wrap_private_key(
        deps.hasher, password, CONSOLE_KEY_NAME, signer.private_key_bytes
    )
    await deps.secrets.put(CONSOLE_KEY_NAME, wrapped)
    return signer


async def _record_console(deps: ConsoleDeps, signer: Ed25519Signer) -> EnrolledDevice:
    """Return the console record for ``signer``, revoking stale ones and creating it if missing."""
    public_key = b64url_encode(signer.public_key_bytes)
    # Every live console record an earlier, interrupted bootstrap may have left behind.
    consoles = [
        device
        for device in await deps.store.list_devices(DeviceStatus.APPROVED)
        if device.loopback_bound
    ]
    # Reuse the record holding this key; revoke any holding another, so one console stays live.
    for existing in consoles:
        # An interrupted bootstrap already recorded this very key: reuse it as is.
        if existing.public_key == public_key:
            return existing
        # A console whose key is not the one in the secret store can never log in; revoking it
        # keeps exactly one live console.
        revoked = trail_kind(DeviceStatus.APPROVED, DeviceStatus.REVOKED)
        event = deps.identity.event(deps.clock, revoked, existing.id, {"reason": CONSOLE_REPLACED})
        await deps.store.update_device_status(
            existing.id, DeviceStatus.APPROVED, DeviceStatus.REVOKED, event
        )
    device = _console_device(deps.clock, public_key)
    await deps.store.put_device(device, _console_event(deps, device))
    return device


def _console_event(deps: ConsoleDeps, console: EnrolledDevice) -> GuardEvent:
    """Build the console's entry event: approved at bootstrap, with what it holds, never its key."""
    payload: dict[str, JsonValue] = {
        "console": True,
        "fingerprint": console.fingerprint,
        "capability_count": len(console.capabilities),
        "spend_cap_usd_per_day": console.spend_cap_usd_per_day,
        "expires_at": None,
        "interactive": console.interactive,
    }
    kind = ENTRY_TRAIL_KINDS[DeviceStatus.APPROVED]
    return deps.identity.event(deps.clock, kind, console.id, payload)


def _console_device(clock: Clock, public_key: str) -> EnrolledDevice:
    """Build the console's record: approved now, loopback-bound, interactive, uncapped."""
    now = clock.now()
    return EnrolledDevice(
        id=new_device_id(clock),
        name=CONSOLE_DEVICE_NAME,
        status=DeviceStatus.APPROVED,
        key_kind=KeyKind.ED25519,
        public_key=public_key,
        interactive=True,
        capabilities=CONSOLE_CAPABILITIES,
        spend_cap_usd_per_day=None,
        expires_at=None,
        loopback_bound=True,
        description=DeviceDescription(
            name=CONSOLE_DEVICE_NAME, platform=sys.platform, user_agent=CONSOLE_USER_AGENT
        ),
        created_at=now,
        approved_at=now,
    )


async def _require_console_blob(secrets: SecretStore) -> bytes:
    """Return the console's wrapped key, or say there is no console yet."""
    blob = await secrets.get(CONSOLE_KEY_NAME)
    if blob is None:
        raise OperatorNotInitialisedError(
            f"There is no console key ({CONSOLE_KEY_NAME!r}) in {secrets!r}; bootstrap the "
            "operator first (hive entrance operator password)."
        )
    return blob
