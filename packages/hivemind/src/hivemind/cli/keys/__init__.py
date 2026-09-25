"""Keep the Hive's Waggle-side Ed25519 keys: ``hive keys list|create|revoke``.

Every Waggle-side principal (the Queen, a Warden, a Worker, a Swarm device; Waggle is the Hive's
bee-to-bee wire protocol) signs with an Ed25519 key per node, and the Hive Stand keeps its own in
the Hive's secret store (roadmap 10.4, 10.8). ``hive keys`` lists them by name with their public
halves and fingerprints (never a private key), mints a named node key for peers to pin, and
revokes one, refusing the Hive's identity key (Supersedure moves it) and the console's key (the
operator password keeps it).

Fits into the Hive:
    Layer 7 (the terminal), inside ``hivemind.cli``. ``app`` is registered on the root ``hive``
    application by ``hivemind.cli.app``. Calls into ``hivemind.common.secrets`` and
    ``waggle.signing``.

Key invariants:
    - This file holds re-exports and ``__all__`` only.

See Also:
    - waggle.signing for the node keys.

Public API:
    - app, PINNED_WARNING: the ``hive keys`` group (commands).
    - KeyEntry, KeyRingError, list_keys, create_key, revoke_key, KEY_SUFFIX, HIVE_IDENTITY,
      NODE_KEY, CONSOLE_DEVICE: the key ring (ring).
"""

from hivemind.cli.keys.commands import PINNED_WARNING, app
from hivemind.cli.keys.ring import (
    CONSOLE_DEVICE,
    HIVE_IDENTITY,
    KEY_SUFFIX,
    NODE_KEY,
    KeyEntry,
    KeyRingError,
    create_key,
    list_keys,
    revoke_key,
)

__all__ = [
    "CONSOLE_DEVICE",
    "HIVE_IDENTITY",
    "KEY_SUFFIX",
    "NODE_KEY",
    "PINNED_WARNING",
    "KeyEntry",
    "KeyRingError",
    "app",
    "create_key",
    "list_keys",
    "revoke_key",
]
