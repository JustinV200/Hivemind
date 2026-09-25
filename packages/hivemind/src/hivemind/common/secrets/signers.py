"""Load, or mint and persist, the Ed25519 signers whose private keys a SecretStore holds.

The Hive's identity is an Ed25519 keypair kept in the secret store (codingrules Appendix C, "Hive
identity"), and the Queen (the orchestrator) signs every Waggle frame (the Hive's wire protocol)
she sends a Virtual Cell (a container or VM the Hive provisions) with it. Minting that key per
process, as phase 5 did, meant a Cell that outlived a Queen restart could never verify the next
Queen (phase 5 open item 5). ``load_or_mint_hive_signer`` fixes that: the first run mints the key
and persists it under ``HIVE_SIGNING_KEY``; every later run reads the same key back. The general
form, ``load_or_mint_signer``, serves any other named Ed25519 key the same way (a Swarm node's,
say); the Hive Stand's (the Queen's machine) console key is wrapped instead
(``hivemind.entrance.enrol.console``).

Fits into the Hive:
    Layer 0 (primitives; imports nothing internal). Called by composition roots:
    ``hivemind.cli.compose.hive.build_hive`` for the Queen's key, and
    ``hivemind.entrance.enrol.console`` for the console's. Calls into the SecretStore it is given
    and ``waggle.signing`` only.

Key invariants:
    - A stored key is used as is and never replaced: once minted, a key changes only by an
      explicit rotation (a later step), never by this module.
    - The private key goes from the signer to the store and nowhere else; no message here ever
      includes it (codingrules section 13).
    - Not safe against two processes minting the same name at the same instant (the last write
      wins); one Hive runs one Queen, and bootstrap runs once, so neither ever races itself.

See Also:
    - waggle.signing for Ed25519Signer and the raw 32-byte private key format stored here.
    - .claude/phase-5-virtual-cells-handoff.md section 5, item 5, for the defect this closes.
"""

from __future__ import annotations

from hivemind.common.errors import SecretStoreError
from hivemind.common.secrets.protocol import SecretStore
from waggle.signing import PRIVATE_KEY_BYTES, Ed25519Signer

HIVE_SIGNING_KEY = "hive.ed25519"  # The Hive's own identity key: signs the Queen's Waggle frames.

__all__ = ["HIVE_SIGNING_KEY", "load_or_mint_hive_signer", "load_or_mint_signer"]


async def load_or_mint_signer(store: SecretStore, name: str) -> Ed25519Signer:
    """Return the Ed25519 signer stored under ``name``, minting and storing one on first use.

    Args:
        store: Where the private key lives (a FileSecretStore on a real Hive Stand).
        name: The secret's name, e.g. ``HIVE_SIGNING_KEY``.

    Returns:
        A signer over the stored key; the same key on every call once it exists.

    Raises:
        SecretStoreError: ``name`` is not a valid secret name, or the secret stored under it is
            not a raw ``PRIVATE_KEY_BYTES``-byte Ed25519 private key.
    """
    # Latency: one small file read (or a dict lookup, in tests).
    stored = await store.get(name)
    if stored is not None:
        return _signer_from(store, name, stored)
    # First use: mint from the CSPRNG and persist before handing the key out, so no frame is ever
    # signed with a key the next process could not read back.
    signer = Ed25519Signer.generate()
    await store.put(name, signer.private_key_bytes)
    return signer


async def load_or_mint_hive_signer(store: SecretStore) -> Ed25519Signer:
    """Return the Hive's own signing key from ``store``, minting it on the Hive's first run.

    Args:
        store: The Hive's secret store, normally a FileSecretStore at ``[hive] secrets_dir``.

    Returns:
        The signer the Queen signs every Virtual Cell frame with; stable across restarts.

    Raises:
        SecretStoreError: The stored ``HIVE_SIGNING_KEY`` secret is not an Ed25519 private key.
    """
    return await load_or_mint_signer(store, HIVE_SIGNING_KEY)


def _signer_from(store: SecretStore, name: str, stored: bytes) -> Ed25519Signer:
    """Build a signer from stored bytes, naming the secret (never its value) when they are wrong."""
    try:
        return Ed25519Signer(stored)
    except ValueError as exc:
        # A truncated or foreign file: refusing loudly beats minting over it, which would
        # silently change the Hive's identity and orphan every Cell that trusts the old key.
        raise SecretStoreError(
            f"Secret {name!r} in {store!r} is not a {PRIVATE_KEY_BYTES}-byte Ed25519 private "
            "key; restore it from a backup, or remove it to mint a new identity."
        ) from exc
