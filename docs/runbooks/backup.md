# Backups: hive backup and hive restore

A backup is one file holding the Hive's stores (roadmap step 13.7, ADR-0064). That covers the
database (`[hive] db`, which holds every store, the Entrance tables and every enrolled device)
and the Basket's blobs. It does **not** hold the secret store. The Hive's identity key and the
console's key stay in `[hive] secrets_dir` and move only by hand, so a stolen backup cannot sign
as your Hive.

## Take a backup

The Hive can keep running. SQLite's online backup API reads a consistent snapshot beside the
Queen's writes.

```
hive backup backups/hive-2026-10-27.tar.gz --manifest hive.toml
```

It prints the archive's SHA-256. Keep that line with the file, since `hive restore --sha256`
checks it. The command never overwrites a file, so give each backup a new name.

Back the secret store up separately, somewhere at least as safe as the machine itself (an
encrypted drive or a password manager's file vault). Without it a backup can only be restored on
a machine that still has the same secret store.

Never copy `hive.sqlite3` by hand while the Hive runs. Its newest commits sit in
`hive.sqlite3-wal`, and a file copy can be torn or out of date.

## Restore

1. Stop the Hive: `hive serve` and any `hive run` on this manifest. `hive restore` holds both of
   the Hive's locks and is refused while either is held.
2. Make sure `[hive] secrets_dir` holds the Hive's secrets. On the same machine it already does.
   On a new machine, copy the directory across by hand, keeping it owner-only.
3. Restore:

   ```
   hive restore backups/hive-2026-10-27.tar.gz --manifest hive.toml --sha256 <the printed hash> --replace
   ```

   `--replace` is needed only when a database is already there. Leave it off on a fresh machine.
4. Start the Hive as usual. Its first start is a Requeening (`docs/runbooks/requeening.md`) from
   the restored stores. Tasks that were running when the backup was taken are placed afresh from
   their Handoffs.

Before writing anything, `hive restore` checks:

- the archive's SHA-256, when `--sha256` is given;
- every member against `backup.json` (size and digest, nothing extra, nothing missing, no odd
  names);
- the database with SQLite's integrity check;
- that the manifest's `[hive] id` is the backed-up Hive's;
- that the secret store holds every secret the backed-up Hive held, and that `hive.ed25519` is the
  same key.

If any check fails, it prints `hive restore refused: ...` and leaves the database as it was. The
restore is recorded on the restored trail as `queen.restored`, with when the backup was taken,
the archive's SHA-256 and file counts.

## What a restore does not do

- It never deletes. Blobs added after the backup stay in the Basket directory, with no row naming
  them.
- It does not bring back anything outside the stores: Virtual Cells, leases and scratch
  directories are Requeening's to reconcile on the next start.
- It will not give the Hive a new identity. If the secret store is lost, the old identity is lost
  with it, and every Cell and device that trusted it must be enrolled again on a new Hive.
