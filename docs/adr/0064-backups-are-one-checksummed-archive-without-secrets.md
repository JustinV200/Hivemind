# ADR-0064: A backup is one checksummed archive of the stores, never of the secrets

- Status: Proposed
- Date: 2026-10-27

## Context

Roadmap 13.7 asks for `hive backup` and `hive restore`, and Supersedure (13.2a) needs them: its
`copy.py` streams a backup over the candidate's `CellSession` and runs `hive restore` on the far
side, with checksums. Since ADR-0006 every store lives in one SQLite file, `[hive] db`, in WAL
mode, the Entrance tables and enrolled devices included. The Basket keeps its blob bytes in a
directory beside it (ADR-0048). The Hive's keys live in the secret store (`[hive] secrets_dir`),
and codingrules 8.16 says the Hive keypair moves only through the secret store.

A running Hive writes its database all the time. Copying `hive.sqlite3` by hand can tear it, or
miss the commits still sitting in its `-wal` file. Restoring by copying a file over it can leave
an old `-wal` beside the new file, which SQLite then replays over the restored pages.

## Decision

**One archive, a gzip'd tar.** Its first member is `backup.json`, a `BackupIndex`: the format
version, when the snapshot was taken, the Hive id, the public half of `hive.ed25519`, the names
of the secrets in the store, the tree names, and every other member with its size and SHA-256.
Then come `hive.sqlite3` and each tree as `<tree>/<path>` (today only `basket`). `hive backup`
prints the archive's own SHA-256 so it can be compared after the file moves.

**SQLite's online backup API, both ways.** The snapshot is read through
`sqlite3.Connection.backup` from a plain connection to the live file, which is safe beside the
Queen's writes and changes nothing on the live file. The snapshot is then switched to rollback
journalling so it is one self-contained file. A restore writes the destination through the same
API into a connection opened the Hive's standard way, so SQLite rewrites the file and its WAL
siblings itself. A database file is never copied by hand.

**No secret in a backup.** The index records only the identity key's public half and the secret
names. A restore refuses unless the destination's secret store holds every name the backed-up
Hive held and its `hive.ed25519` is the same key. The operator moves the secret store by hand
(or Supersedure's preflight requires it on the candidate). A stolen backup holds the Hive's data
but cannot sign as the Hive.

**A restore trusts nothing in the archive.** The index must come first and be small and valid.
Every member must be a regular file the index lists, once (compared case-folded), at its listed
size and digest. Each is written to a path built from the validated name, never by `tar.extract`.
The snapshot must pass `PRAGMA integrity_check`. Anything else refuses the whole archive before
the destination is touched.

**Only offline, only the same Hive.** `hive restore` holds the serve lock and the Queen lock for
its whole run, refuses an existing database without `--replace`, and refuses a backup whose Hive
id differs from the manifest's. It records `queen.restored` on the restored trail as the human,
with the backup's time, the archive's SHA-256 and counts. The catalogue classifies it as an
offline file operation, since no Queen runs and the file system is the only boundary.

**The primitive lives in `hivemind.common.backup`.** It is plain SQLite, files and checksums.
Placing it at Layer 0 lets the CLI use it today and the Queen's Supersedure use it later, without
reaching up into `cli`.

## Consequences

- Supersedure's copy can stream one file and check one digest, then check every member again on
  the far side.
- A restore never deletes. Blobs written after the backup stay as orphans with no row, which is
  harmless. A blob evicted while a backup was copying it is skipped, so its row (if the snapshot
  still has one) reads as missing until it is shipped again.
- A restore needs disk for the extraction beside the database, about the size of the archive
  uncompressed.
- Recovering a Hive whose secret store is lost needs the old store. A restore will not mint a new
  identity over the old stores.

## Alternatives considered

- **`VACUUM INTO`.** It also writes a consistent copy, but it rebuilds the file and takes longer.
  The backup API is the documented way to copy a live database.
- **Encrypting the keypair into the archive under a passphrase.** One more secret to manage, and
  the roadmap keeps the keypair inside the secret store.
- **A directory instead of one file.** It is harder to stream and checksum as a unit for
  Supersedure.

## See also

- ADR-0006 (one SQLite file per Hive), ADR-0048 (the Basket's blob directory).
- `docs/runbooks/backup.md`.
