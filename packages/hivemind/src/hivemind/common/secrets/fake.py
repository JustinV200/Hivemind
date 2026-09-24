"""Provide MemorySecretStore: an in-process SecretStore for tests, demos and ``hive doctor``.

Codingrules 14.4 keeps fakes beside their protocol in ``src/``, honest and production-quality,
because demo paths use them as well as tests. This one holds secrets in a dict for the life of the
process and applies exactly the name and size rules ``hivemind.common.secrets.file.
FileSecretStore`` does (both call ``hivemind.common.secrets.protocol``'s checks), so a test that
passes against it exercises the same contract a real Hive Stand relies on.

Fits into the Hive:
    Layer 0 (primitives; imports nothing internal). Injected wherever a composition root would
    otherwise open a ``FileSecretStore``: ``hivemind.cli.compose.hive.build_hive(secrets=...)``
    in tests, and ``hivemind.entrance.enrol.console`` tests.

Key invariants:
    - Behaves exactly like FileSecretStore under the shared contract suite
      (``tests/contracts/test_secret_store_contract.py``).
    - ``repr`` reports how many secrets are held, never a name's value.

See Also:
    - hivemind.common.secrets.protocol for SecretStore and the checks both stores apply.
"""

from __future__ import annotations

from hivemind.common.secrets.protocol import check_secret_name, check_secret_value

__all__ = ["MemorySecretStore"]


class MemorySecretStore:
    """Hold secrets in a dict for the life of the process; nothing touches the disk.

    No lock guards the dict: every method reads or writes it without an ``await`` in between, so
    on one event loop no two operations can interleave.
    """

    def __init__(self) -> None:
        """Create an empty store."""
        self._secrets: dict[str, bytes] = {}

    def __repr__(self) -> str:
        """Report how many secrets are held; never a value."""
        return f"MemorySecretStore(secrets={len(self._secrets)})"

    async def get(self, name: str) -> bytes | None:
        """Return the secret stored under ``name``; see SecretStore.get."""
        check_secret_name(name)
        return self._secrets.get(name)

    async def put(self, name: str, value: bytes) -> None:
        """Store ``value`` under ``name``; see SecretStore.put."""
        check_secret_name(name)
        check_secret_value(value)
        # bytes() copies a bytearray or memoryview a caller might mutate later, so the stored
        # secret is exactly what was put, the same guarantee a file on disk gives.
        self._secrets[name] = bytes(value)

    async def delete(self, name: str) -> None:
        """Remove the secret stored under ``name``, if any; see SecretStore.delete."""
        check_secret_name(name)
        self._secrets.pop(name, None)

    async def names(self) -> tuple[str, ...]:
        """Return every stored secret's name, sorted; see SecretStore.names."""
        return tuple(sorted(self._secrets))
