"""Back the Hive up and restore it: ``hive backup`` and ``hive restore`` (roadmap 13.7).

``hive backup OUT`` writes one checksummed archive of the Hive's stores (the database, every
store, the Entrance tables and enrolled devices in it, and the Basket's blobs) through
``hivemind.common.backup``, stamped with the Hive's id, its identity key's public half and its
secret store's names. ``hive restore ARCHIVE`` checks an archive end to end, refuses a different
Hive or a secret store that cannot carry it, and puts it back under the Hive's serve and Queen
locks, recording ``queen.restored``. The Hive keypair moves only through the secret store.

Fits into the Hive:
    Layer 7 (the terminal), inside ``hivemind.cli``. ``backup_command`` and ``restore_command``
    are registered on the root ``hive`` application by ``hivemind.cli.app``.

Key invariants:
    - This file holds re-exports and ``__all__`` only.

See Also:
    - docs/runbooks/backup.md and
      docs/adr/0064-backups-are-one-checksummed-archive-without-secrets.md.

Public API:
    - backup_command, restore_command: the two commands (commands).
    - BASKET_TREE, RESTORED_KIND, backup_source, check_destination,
      record_restored, secret_store: the Hive's side of a backup (hive).
"""

from hivemind.cli.backup.commands import backup_command, restore_command
from hivemind.cli.backup.hive import (
    BASKET_TREE,
    RESTORED_KIND,
    backup_source,
    check_destination,
    record_restored,
    secret_store,
)

__all__ = [
    "BASKET_TREE",
    "RESTORED_KIND",
    "backup_command",
    "backup_source",
    "check_destination",
    "record_restored",
    "restore_command",
    "secret_store",
]
