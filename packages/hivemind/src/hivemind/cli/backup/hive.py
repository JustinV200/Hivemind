"""Tie a backup to one Hive: what its manifest names, what its secret store holds, its trail record.

`hivemind.common.backup` knows databases, file trees and checksums; this module knows the Hive.
A backup of it is `[hive] db` plus the Basket's blob directory (`[basket] root`, or `basket`
beside the database), stamped with the Hive's id, the public half of its identity key
(`hive.ed25519`) and the names in its secret store. The private key is never read for the archive
(only its public half is derived, in memory) and never written into it: a restore moves the
stores, and the keys move only through the secret store (codingrules 8.16). So before a restore
writes anything, the destination must be the same Hive (its manifest's `[hive] id`), its secret
store must hold every secret the backed-up Hive held, and its `hive.ed25519` must be the same key,
or every Cell and device that trusts the Hive would stop trusting it. After a restore,
`queen.restored` is recorded on the restored trail, as the human, with checksums and counts.

Fits into the Hive:
    Layer 7 (the terminal), inside `hivemind.cli.backup`. Called by `.commands`. Calls into
    `hivemind.common.backup`, `hivemind.common.secrets`, `hivemind.common.sqlite`,
    `hivemind.guard.policy` (OPERATOR_ID), `hivemind.manifest`, `hivemind.pheromone` and
    `waggle.signing`.

Key invariants:
    - No private key, and no secret's value, is returned, printed, logged or archived here.
    - `check_destination` refuses before anything is written; it never repairs a store.

See Also:
    - docs/runbooks/backup.md for moving the secret store by hand.
    - hivemind.common.secrets.signers for the identity key's name and shape.
"""

from __future__ import annotations

from datetime import UTC, datetime

from hivemind.common.backup import BackupIndex, BackupSource, OpenedBackup
from hivemind.common.errors import BackupError
from hivemind.common.secrets import HIVE_SIGNING_KEY, FileSecretStore, SecretStore
from hivemind.common.sqlite import connect, transaction
from hivemind.guard.policy import OPERATOR_ID
from hivemind.manifest import HiveManifest
from hivemind.pheromone import QueenEvent, apply_pheromone_migrations, insert_event
from waggle.clock import Clock
from waggle.ids import new_event_id
from waggle.signing import Ed25519Signer, public_key_hex

BASKET_TREE = "basket"  # The archive's name for the Basket's blob directory.
RESTORED_KIND = "queen.restored"  # Recorded on the restored trail once the restore is done.

__all__ = [
    "BASKET_TREE",
    "RESTORED_KIND",
    "backup_source",
    "check_destination",
    "record_restored",
    "secret_store",
]


def secret_store(manifest: HiveManifest) -> FileSecretStore:
    """The Hive's secret store, as `[hive] secrets_dir` names it."""
    return FileSecretStore(manifest.resolve_path(manifest.hive.secrets_dir))


async def backup_source(manifest: HiveManifest) -> BackupSource:
    """Everything `write_backup` copies and records for this Hive.

    Args:
        manifest: The Hive's loaded manifest.

    Returns:
        The database, the Basket tree, the Hive id, the identity key's public half (None before
        the Hive minted one) and the secret store's names.

    Raises:
        BackupError: The stored identity key is not an Ed25519 private key.
    """
    store = secret_store(manifest)
    # Latency: a directory listing and one small file read on a local disk.
    return BackupSource(
        db=manifest.resolve_path(manifest.hive.db),
        hive_id=manifest.hive.id,
        trees={BASKET_TREE: manifest.basket_root()},
        identity_key=await _identity_key(store),
        secret_names=await store.names(),
    )


async def check_destination(index: BackupIndex, manifest: HiveManifest) -> None:
    """Refuse a restore into a different Hive, or one whose secret store cannot carry the stores.

    Args:
        index: The checked backup's index.
        manifest: The destination Hive's manifest.

    Raises:
        BackupError: The Hive ids differ, a secret the backup's Hive held is missing here, or
            this store's `hive.ed25519` is a different key from the backup's.
    """
    if index.hive_id != manifest.hive.id:
        raise BackupError(
            f"The backup is of Hive {index.hive_id}, not this manifest's {manifest.hive.id}."
        )
    store = secret_store(manifest)
    # Names only: the operator moves each missing secret through the secret store by hand.
    missing = sorted(set(index.secret_names) - set(await store.names()))
    if missing:
        raise BackupError(
            f"The secret store at {store.root} lacks {', '.join(missing)}; move the Hive's "
            "secret store first (docs/runbooks/backup.md)."
        )
    if index.identity_key is not None and await _identity_key(store) != index.identity_key:
        raise BackupError(
            f"The secret store's {HIVE_SIGNING_KEY} is not the key the backed-up Hive signed "
            "with; restoring would change the Hive's identity."
        )


def record_restored(
    manifest: HiveManifest, opened: OpenedBackup, written: int, clock: Clock
) -> None:
    """Record `queen.restored` on the restored database's trail, as the human.

    Args:
        manifest: The destination Hive's manifest (its id, node id and database).
        opened: The backup that was restored.
        written: How many Basket files the restore wrote.
        clock: Stamps the event.
    """
    db = manifest.resolve_path(manifest.hive.db)
    connection = connect(db)
    try:
        # A backup from before a trail migration still gets the current schema before the insert.
        apply_pheromone_migrations(connection, clock)
        with transaction(connection):
            insert_event(connection, _event(manifest, opened, written, clock))
    finally:
        connection.close()


async def _identity_key(store: SecretStore) -> str | None:
    """The public half of the store's `hive.ed25519`, hex; None when there is none."""
    private = await store.get(HIVE_SIGNING_KEY)
    if private is None:
        return None
    try:
        # The private bytes live only for this derivation; only the public half leaves here.
        return public_key_hex(Ed25519Signer(private).public_key_bytes)
    except ValueError as exc:
        raise BackupError(f"{HIVE_SIGNING_KEY} in {store!r} is not an Ed25519 key.") from exc


def _event(manifest: HiveManifest, opened: OpenedBackup, written: int, clock: Clock) -> QueenEvent:
    """Build `queen.restored`: when the backup was taken, its checksum, and counts only."""
    taken: datetime = opened.index.created_at.astimezone(UTC)
    return QueenEvent(
        id=new_event_id(clock),
        hive_id=manifest.hive.id,
        node_id=manifest.hive.node_id,
        at=clock.now(),
        actor=OPERATOR_ID,  # Only a person runs `hive restore`.
        kind=RESTORED_KIND,
        subject_id=manifest.hive.id,
        payload={
            "backup_created_at": taken.isoformat(),
            "archive_sha256": opened.sha256,
            "files": len(opened.index.files),
            "basket_files_written": written,
        },
    )
