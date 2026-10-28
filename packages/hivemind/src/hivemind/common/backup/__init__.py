"""Back a Hive up into one checksummed archive, and check and restore one.

Roadmap 13.7. A backup is a gzip'd tar whose first member, `backup.json`, lists every other member
with its size and SHA-256: a consistent snapshot of the Hive's one SQLite database taken through
SQLite's online backup API (every store, the Entrance tables and enrolled devices with it), and
any named file trees (the Basket's blob bytes). No secret is in it: the index records the public
half of the Hive's identity key and the names of the secrets the Hive held, and the private key
moves only through the secret store (codingrules 8.16). Reading one trusts nothing: every member
must match the index, and the snapshot must pass SQLite's integrity check, before a restore
writes the destination back through the same backup API. `write` makes an archive, `read`
checks and extracts one, `restore` puts one back, `index` is the format and its name rules,
`digest` the chunked hashing they share. This file is the package's face.

Fits into the Hive:
    Layer 0 (primitives; imports nothing internal beyond waggle). Used by `hive backup` and
    `hive restore` (`hivemind.cli.backup`), and by Supersedure's copy (roadmap 13.2a).

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - No function here reads a secret store or writes a secret's value anywhere.

See Also:
    - docs/adr/0064-backups-are-one-checksummed-archive-without-secrets.md.
    - docs/runbooks/backup.md for the operator's side.

Public API:
    - BackupSource, BackupReceipt, write_backup: snapshot a Hive into a new archive (write).
    - OpenedBackup, open_backup: check an archive end to end and extract it (read).
    - restore_backup: put an opened backup back over a database and its trees (restore).
    - BackupIndex, BackupFile, DB_MEMBER, INDEX_MEMBER, FORMAT_VERSION, check_member_path: the
      archive's index and the rules its member names keep (index).
    - sha256_file: a file's SHA-256 in bounded memory (digest).
"""

from hivemind.common.backup.digest import sha256_file
from hivemind.common.backup.index import (
    DB_MEMBER,
    FORMAT_VERSION,
    INDEX_MEMBER,
    BackupFile,
    BackupIndex,
    check_member_path,
)
from hivemind.common.backup.read import OpenedBackup, open_backup
from hivemind.common.backup.restore import restore_backup
from hivemind.common.backup.write import BackupReceipt, BackupSource, write_backup

__all__ = [
    "DB_MEMBER",
    "FORMAT_VERSION",
    "INDEX_MEMBER",
    "BackupFile",
    "BackupIndex",
    "BackupReceipt",
    "BackupSource",
    "OpenedBackup",
    "check_member_path",
    "open_backup",
    "restore_backup",
    "sha256_file",
    "write_backup",
]
