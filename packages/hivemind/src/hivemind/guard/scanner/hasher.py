"""Hash flagged content under a node-local key: the scanner's HMAC, minted on first use.

When the untrusted-content scanner flags a text (roadmap step 10.6b), the trail records a hash of
it, never the text (codingrules section 12). A plain hash would let anyone holding the trail
confirm a guess ("was it this exact string?") by hashing the guess; an HMAC under a key only this
node holds cannot be checked that way (ADR-0043: "a keyed hash of the content ... so the hash
cannot be used to confirm a guess"). The key lives in the secret store (`hivemind.common.secrets`)
under `SCANNER_KEY_NAME`, never in the manifest, a log or the trail; it is minted from the CSPRNG
the first time a text is flagged and read back on every later run, so a Hive Stand's hashes stay
comparable across restarts. A Virtual Cell's Warden, which has no Hive secret store, is built with
an in-memory store instead: its key lives and dies with the Cell, which is what a Night Veil Cell
(the anonymous tier, torn down without retention) needs anyway.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.guard.scanner`. Held by
    `hivemind.guard.scanner.scanner.ContentScanner`. Calls into `hivemind.common.secrets`
    (SecretStore), `hivemind.common.errors` (SecretStoreError) and the standard library (`hmac`,
    `hashlib`, `secrets`).

Key invariants:
    - The key is read or minted at most once per hasher, under a lock, so two concurrent first
      flags never mint two different keys.
    - A stored key of the wrong size is refused loudly (SecretStoreError), never silently replaced:
      replacing it would make every earlier hash on the trail unmatchable.
    - Nothing here returns, logs or reprs the key.

See Also:
    - hivemind.common.secrets for SecretStore and the name rules SCANNER_KEY_NAME follows.
    - hivemind.common.secrets.signers for the same load-or-mint pattern applied to Ed25519 keys.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets

from hivemind.common.errors import SecretStoreError
from hivemind.common.secrets import SecretStore
from hivemind.guard.scanner.verdict import HASH_PREFIX

SCANNER_KEY_NAME = "guard.untrusted-content.hmac"  # The secret-store name of the HMAC key.
KEY_BYTES = 32  # 256 bits: HMAC-SHA256's own block-size-safe key length.
# A secret-store read is one small local file (or a dict lookup); a store that has not answered in
# this long is broken, and a scan must fail rather than hang a bee's tool call.
KEY_READ_TIMEOUT_S = 5.0

__all__ = ["KEY_BYTES", "KEY_READ_TIMEOUT_S", "SCANNER_KEY_NAME", "ContentHasher"]


class ContentHasher:
    """Compute `hmac-sha256:<hex>` digests under this node's scanner key.

    Owns one piece of mutable state, the key once loaded (`_key`), guarded by `_lock` for its
    first load so concurrent scans agree on one key (codingrules section 11).
    """

    def __init__(self, store: SecretStore) -> None:
        """Build a hasher whose key lives in `store` under `SCANNER_KEY_NAME`.

        Args:
            store: The node's secret store: a FileSecretStore at `[hive] secrets_dir` on the Hive
                Stand, a MemorySecretStore inside a Virtual Cell or a test.
        """
        self._store = store
        self._key: bytes | None = None
        # Guards the first read-or-mint of `_key`; once set it is only ever read.
        self._lock = asyncio.Lock()

    def __repr__(self) -> str:
        """Say whether the key is loaded; never the key."""
        return f"ContentHasher(key_loaded={self._key is not None})"

    async def digest(self, text: str) -> str:
        """Return the keyed hash of `text`, loading or minting the key on first use.

        Args:
            text: The whole text to hash (not only the part the scanner matched).

        Returns:
            `hmac-sha256:` followed by 64 lowercase hex characters.

        Raises:
            SecretStoreError: The stored key is not `KEY_BYTES` long, or the store did not answer
                within `KEY_READ_TIMEOUT_S`.
        """
        key = await self._key_bytes()
        mac = hmac.new(key, text.encode("utf-8", errors="surrogatepass"), hashlib.sha256)
        return f"{HASH_PREFIX}{mac.hexdigest()}"

    async def _key_bytes(self) -> bytes:
        """Return the key, reading it from the store or minting it there the first time."""
        if self._key is not None:
            return self._key
        async with self._lock:
            # Re-checked under the lock: a concurrent first scan may have loaded it meanwhile.
            if self._key is None:
                self._key = await self._load_or_mint()
            return self._key

    async def _load_or_mint(self) -> bytes:
        """Read the stored key, or mint and persist a fresh one before it is ever used."""
        try:
            # A local secret-store read, milliseconds; bounded so a broken store cannot hang.
            async with asyncio.timeout(KEY_READ_TIMEOUT_S):
                stored = await self._store.get(SCANNER_KEY_NAME)
                if stored is None:
                    stored = secrets.token_bytes(KEY_BYTES)
                    await self._store.put(SCANNER_KEY_NAME, stored)
        except TimeoutError as exc:
            raise SecretStoreError(
                f"The secret store did not return {SCANNER_KEY_NAME!r} within "
                f"{KEY_READ_TIMEOUT_S}s; flagged content cannot be hashed."
            ) from exc
        if len(stored) != KEY_BYTES:
            raise SecretStoreError(
                f"Secret {SCANNER_KEY_NAME!r} is not a {KEY_BYTES}-byte key; restore it from a "
                "backup, or remove it to mint a new one (earlier hashes will no longer match)."
            )
        return stored
