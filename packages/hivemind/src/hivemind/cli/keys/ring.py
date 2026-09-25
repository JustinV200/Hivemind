"""List, create and revoke the Waggle-side Ed25519 keys the Hive keeps in its secret store.

Every Waggle-side principal (the Queen, a Warden, a Worker, a Swarm device; Waggle is the Hive's
bee-to-bee wire protocol) signs its frames with an Ed25519 key per node (``waggle.signing``), and
the Hive Stand keeps its own in the Hive's secret store under ``<name>.ed25519``: ``hive.ed25519``
is the Hive's identity (every Virtual Cell verifies the Queen's frames with it), and further named
node keys can be minted for peers to pin. A key's public half is what a peer pins (hex, the form
``waggle.signing`` gives a human and a manifest), and its fingerprint is how two people compare it;
its private half never leaves the store and is never shown. Two names are not this module's to
revoke: the Hive's identity key (moving it is Supersedure's job, roadmap phase 13, because every
Cell and device that trusts the Hive trusts it) and the Hive Stand console's device key, which is
wrapped under the operator password and belongs to ``hive entrance operator password``. Revoking
any other key deletes its private half, so nothing on this Hive Stand can sign as it again; a peer
that pinned its public half keeps trusting it until that pin is removed, because Waggle has no
revocation list in this phase. The trail's vocabulary has no key-lifecycle kind yet either, so the
change is logged here (name and fingerprint, never key material), not recorded on the trail.

Fits into the Hive:
    Layer 7 (the terminal), inside ``hivemind.cli.keys``. Used by ``hive keys``
    (``hivemind.cli.keys.commands``). Calls into ``hivemind.common.secrets``, ``waggle.signing``
    and ``hivemind.entrance`` (the fingerprint, the console key's name).

Key invariants:
    - No private key material is returned, printed or logged; only public keys and fingerprints.
    - The Hive's identity key and the console key are never created or deleted here.

See Also:
    - waggle.signing for the node keys and their hex form.
    - docs/adr/0005-waggle-envelope-signing-and-offline-outbox.md for the per-node key decision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar

from hivemind.common.errors import ConflictError, NotFoundError
from hivemind.common.logging import get_logger
from hivemind.common.secrets import HIVE_SIGNING_KEY, SecretStore
from hivemind.entrance.auth import key_fingerprint
from hivemind.entrance.enrol import CONSOLE_KEY_NAME
from waggle.signing import Ed25519Signer, public_key_hex

KEY_SUFFIX = ".ed25519"  # Every Ed25519 key in the secret store is named <name>.ed25519.
# A key's name without its suffix: the suffix brings it to the secret store's 64-character limit.
KEY_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,55}")
HIVE_IDENTITY = "hive identity"  # The role hive.ed25519 plays.
CONSOLE_DEVICE = "console device (wrapped under the operator password)"
NODE_KEY = "node key"  # Any other Ed25519 key minted here.
UNREADABLE = "not a raw Ed25519 key"  # A file under the name that is not one.

log = get_logger(__name__)

__all__ = [
    "CONSOLE_DEVICE",
    "HIVE_IDENTITY",
    "KEY_SUFFIX",
    "NODE_KEY",
    "KeyEntry",
    "KeyRingError",
    "create_key",
    "list_keys",
    "revoke_key",
]


class KeyRingError(ConflictError):
    """Raise when a key cannot be created or revoked as asked (reserved, taken, or absent)."""

    code: ClassVar[str] = "hivemind.cli.keys.refused"


class KeyNotFoundError(KeyRingError, NotFoundError):
    """Raise when no key goes by the name given."""

    code: ClassVar[str] = "hivemind.cli.keys.not_found"


@dataclass(frozen=True, slots=True)
class KeyEntry:
    """One Ed25519 key in the secret store, as ``hive keys`` shows it: never its private half.

    Attributes:
        name: The secret's name, e.g. ``hive.ed25519``.
        role: What it is: the Hive's identity, the console's wrapped key, a node key.
        public_key_hex: Its public key, 64 hex characters, for a peer to pin; None when it
            cannot be read without the operator password (the console's) or is not a key.
        fingerprint: The same key's short fingerprint, for comparing by eye.
    """

    name: str
    role: str
    public_key_hex: str | None
    fingerprint: str | None


async def list_keys(store: SecretStore) -> tuple[KeyEntry, ...]:
    """Every Ed25519 key in the store, by name, with its public half and fingerprint.

    Args:
        store: The Hive's secret store.

    Returns:
        One entry per ``*.ed25519`` secret, sorted by name; other secrets are not keys of this
        kind and are left out.
    """
    # Latency: one directory listing and a few small reads on a local disk.
    names = [name for name in await store.names() if name.endswith(KEY_SUFFIX)]
    return tuple([await _entry(store, name) for name in names])


async def create_key(store: SecretStore, name: str) -> KeyEntry:
    """Mint a new named node key and keep it in the store.

    Args:
        store: The Hive's secret store.
        name: The key's name, with or without ``.ed25519``.

    Returns:
        The new key's entry.

    Raises:
        KeyRingError: The name is not a key name, is reserved, or is taken.
    """
    secret = _secret_name(name.removesuffix(KEY_SUFFIX))
    if secret in (HIVE_SIGNING_KEY, CONSOLE_KEY_NAME):
        raise KeyRingError(f"{secret} is the Hive's own; it is minted by the Hive, not by name.")
    if await store.get(secret) is not None:
        raise KeyRingError(f"A key named {secret} already exists; revoke it first or pick another.")
    signer = Ed25519Signer.generate()
    await store.put(secret, signer.private_key_bytes)
    entry = _public_entry(secret, NODE_KEY, signer)
    log.info("keys.created", name=secret, fingerprint=entry.fingerprint)
    return entry


async def revoke_key(store: SecretStore, name: str) -> KeyEntry:
    """Delete a node key's private half; the Hive's identity and the console key are refused.

    Args:
        store: The Hive's secret store.
        name: The key's name, with or without ``.ed25519``.

    Returns:
        The entry of the key revoked (public half and fingerprint, for the peers that pinned it).

    Raises:
        KeyRingError: It is the Hive's identity key or the console key.
        KeyNotFoundError: No key goes by that name.
    """
    secret = _secret_name(name.removesuffix(KEY_SUFFIX))
    if secret == HIVE_SIGNING_KEY:
        raise KeyRingError(
            f"{secret} is the Hive's identity: every Cell and device that trusts the Hive trusts "
            "it, so it moves only with the Hive Stand, by Supersedure, never by revocation."
        )
    if secret == CONSOLE_KEY_NAME:
        raise KeyRingError(
            f"{secret} is the Hive Stand console's device key; replace it with hive entrance "
            "operator password --reset."
        )
    if await store.get(secret) is None:
        raise KeyNotFoundError(f"No key named {secret}; hive keys list shows them.")
    entry = await _entry(store, secret)
    await store.delete(secret)
    log.info("keys.revoked", name=secret, fingerprint=entry.fingerprint)
    return entry


async def _entry(store: SecretStore, secret: str) -> KeyEntry:
    """Describe one stored key by its public half, never its private one."""
    if secret == CONSOLE_KEY_NAME:
        # Wrapped under the operator password: its public half is not readable without it.
        return KeyEntry(secret, CONSOLE_DEVICE, None, None)
    stored = await store.get(secret)
    try:
        signer = Ed25519Signer(stored or b"")
    except ValueError:
        return KeyEntry(secret, UNREADABLE, None, None)
    return _public_entry(secret, HIVE_IDENTITY if secret == HIVE_SIGNING_KEY else NODE_KEY, signer)


def _public_entry(secret: str, role: str, signer: Ed25519Signer) -> KeyEntry:
    """An entry from a key's public half."""
    public = signer.public_key_bytes
    return KeyEntry(secret, role, public_key_hex(public), key_fingerprint(public))


def _secret_name(name: str) -> str:
    """The secret's name for a key name; refuse one that is not a key name."""
    if KEY_NAME.fullmatch(name) is None:
        raise KeyRingError(
            f"{name!r} is not a key name: up to 56 of a-z, 0-9, - and _, starting with a letter "
            "or digit."
        )
    return name + KEY_SUFFIX
