"""Hold the Hive's secrets: the SecretStore protocol, its two stores and the Hive's signing key.

Key material the Hive itself mints (the Hive's Ed25519 identity key, the device key of the console
on the Hive Stand, the machine the Hive's orchestrator runs on) never goes in the manifest, the
code, a log line or the Pheromone Trail (the audit log); it lives in a secret store addressed by
name (codingrules section 13). ``protocol`` defines the store and the name and size rules; ``file``
is the durable store (one owner-only file per secret, written atomically); ``fake`` is the in-memory
one for tests and demos; ``signers`` loads, or mints and persists, the Ed25519 keys kept there. This
file is the package's face.

Fits into the Hive:
    Layer 0 (primitives; imports nothing internal beyond waggle). Used by composition roots:
    ``hivemind.cli.compose.hive`` (the Queen's signing key) and ``hivemind.entrance.enrol.console``
    (the console's device key).

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - No name exported here ever returns, logs or reprs a secret's value except
      ``SecretStore.get`` and ``waggle.signing.Ed25519Signer.private_key_bytes``, its one reader.

See Also:
    - .claude/codingrules.md section 13 for the secrets rule and Appendix C for the Hive identity
      row.
    - hivemind.manifest.schema.core.HiveSection.secrets_dir for where FileSecretStore's directory
      is configured.

Public API:
    - SecretStore: the protocol every secret store implements (get, put, delete, names).
    - SECRET_NAME_PATTERN, MAX_SECRET_BYTES, check_secret_name, check_secret_value: the name
      grammar and size bound every store enforces.
    - FileSecretStore, DIRECTORY_MODE, FILE_MODE: the durable, owner-only, atomic store.
    - MemorySecretStore: the in-process store for tests and demos.
    - HIVE_SIGNING_KEY, load_or_mint_hive_signer, load_or_mint_signer: the persisted Ed25519
      signers kept in a store.
"""

from hivemind.common.secrets.fake import MemorySecretStore
from hivemind.common.secrets.file import DIRECTORY_MODE, FILE_MODE, FileSecretStore
from hivemind.common.secrets.protocol import (
    MAX_SECRET_BYTES,
    SECRET_NAME_PATTERN,
    SecretStore,
    check_secret_name,
    check_secret_value,
)
from hivemind.common.secrets.signers import (
    HIVE_SIGNING_KEY,
    load_or_mint_hive_signer,
    load_or_mint_signer,
)

__all__ = [
    "DIRECTORY_MODE",
    "FILE_MODE",
    "HIVE_SIGNING_KEY",
    "MAX_SECRET_BYTES",
    "SECRET_NAME_PATTERN",
    "FileSecretStore",
    "MemorySecretStore",
    "SecretStore",
    "check_secret_name",
    "check_secret_value",
    "load_or_mint_hive_signer",
    "load_or_mint_signer",
]
