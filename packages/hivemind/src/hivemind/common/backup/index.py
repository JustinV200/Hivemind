"""Describe a Hive backup archive: its index, its member names and the rules a member must keep.

A backup (roadmap 13.7) is one gzip'd tar file. Its first member is `backup.json`, the index this
module defines: which Hive it came from, when, the public half of the Hive's identity key, the
names (never the values) of the secrets that Hive held, and every other member with its size and
SHA-256. Then come `hive.sqlite3`, a consistent snapshot of the Hive's one database (every store,
the Entrance tables and the enrolled devices included, ADR-0006), and each file tree the caller
named (the Basket's blob bytes), as `<tree>/<relative path>`. The archive is read back by a
program that trusts nothing in it, so the shape of a member's name is checked here, once, for the
writer and the reader alike: lowercase tree names, plain path parts, no `..`, no absolute path,
no dotfile, and no two names that would collide on a case-insensitive file system.

Fits into the Hive:
    Layer 0 (primitives; imports nothing internal beyond waggle). Built by
    `hivemind.common.backup.write`, validated by `hivemind.common.backup.read` before a single
    byte is extracted. Its fields are what `hive restore` checks against the destination Hive.

Key invariants:
    - Every path an index lists passes `check_member_path`, is listed once (ignoring case), and
      `hive.sqlite3` is always one of them; so every path an index lists is safe to join under
      an extraction directory.
    - No secret value is ever part of an index: `identity_key` is a public key and
      `secret_names` are names.

See Also:
    - docs/adr/0064-backups-are-one-checksummed-archive-without-secrets.md for the format.
    - hivemind.common.secrets for the store whose names `secret_names` lists.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hivemind.common.errors import BackupError
from waggle.messages.base import SHA256_PATTERN, HiveIdField, UtcDatetime

FORMAT_VERSION = 1  # Bumped only when a reader of the old format could misread the new one.
INDEX_MEMBER = "backup.json"  # Always the archive's first member, so a reader can stream the rest.
DB_MEMBER = "hive.sqlite3"  # The database snapshot's name inside the archive.
MAX_INDEX_BYTES = 16 * 1024 * 1024  # About 100,000 listed files; bounds what a reader parses.
# A tree is named by the writer's caller (`basket`), never by a user: short, lowercase, plain.
TREE_NAME = re.compile(r"[a-z][a-z0-9_]{0,31}")
# One path part: no leading dot (so never `.`, `..` or a hidden temp file), no separator, no
# drive colon, nothing a shell or Windows treats specially.
PATH_PART = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}")
MAX_DEPTH = 8  # Parts below a tree's root; the Basket's own layout needs two.

__all__ = [
    "DB_MEMBER",
    "FORMAT_VERSION",
    "INDEX_MEMBER",
    "MAX_INDEX_BYTES",
    "BackupFile",
    "BackupIndex",
    "check_member_path",
]

# Frozen and extras-forbidding: an index is read from an untrusted file (codingrules 8.5, 9).
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


class BackupFile(BaseModel):
    """One member of a backup archive besides the index: where it goes, how big, its digest."""

    model_config = _MODEL_CONFIG

    path: str = Field(description="The member's name: hive.sqlite3, or <tree>/<relative path>.")
    size: int = Field(ge=0, description="The member's length in bytes.")
    sha256: str = Field(pattern=SHA256_PATTERN, description="The member's SHA-256, lowercase hex.")


class BackupIndex(BaseModel):
    """`backup.json`: what a backup archive holds and where it came from.

    Crosses the file boundary twice: written into the archive by `write_backup`, read back by
    `open_backup` before anything else, and checked by `hive restore` against the destination.
    """

    model_config = _MODEL_CONFIG

    format: Literal[1] = Field(description="The archive format's version (FORMAT_VERSION).")
    created_at: UtcDatetime = Field(description="When the database snapshot was taken.")
    hive_id: HiveIdField = Field(description="The Hive the stores belong to.")
    identity_key: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        description="The public half of the Hive's identity key (hive.ed25519), hex; None when "
        "the Hive had not minted one yet. The private half is never in a backup.",
    )
    secret_names: tuple[str, ...] = Field(
        default=(),
        description="The names of the secrets the Hive's secret store held, so a restore can say "
        "which ones the destination still needs. Names only, never values.",
    )
    trees: tuple[str, ...] = Field(
        default=(), description="The file trees the archive carries, by name (e.g. basket)."
    )
    files: tuple[BackupFile, ...] = Field(
        description="Every member but the index, the database snapshot first."
    )

    @model_validator(mode="after")
    def _paths_are_safe_and_unique(self) -> BackupIndex:
        """Refuse an index whose member list could escape, collide or miss the database."""
        # Tree names are checked first: check_member_path reads them.
        for tree in self.trees:
            if TREE_NAME.fullmatch(tree) is None:
                raise ValueError(f"tree name {tree!r} is not a plain lowercase name")
        seen: set[str] = set()
        # Each path once, compared case-folded: two names that differ only in case would land on
        # one file on Windows or macOS, and the second would silently replace the first.
        for entry in self.files:
            check_member_path(entry.path, self.trees)
            folded = entry.path.casefold()
            if folded in seen:
                raise ValueError(f"member {entry.path!r} is listed twice")
            seen.add(folded)
        if DB_MEMBER not in seen:
            raise ValueError(f"the index lists no {DB_MEMBER}")
        return self


def check_member_path(path: str, trees: tuple[str, ...]) -> None:
    """Refuse a member name that is not the database or a plain path inside a named tree.

    Args:
        path: The member's name, `/`-separated as tar stores it.
        trees: The tree names the archive declares.

    Raises:
        BackupError: The name is the index's own, outside every declared tree, too deep, or has a
            part `PATH_PART` refuses (`..`, an absolute path, a drive, a dotfile, a backslash).
    """
    # The database is the one member outside every tree.
    if path == DB_MEMBER:
        return
    tree, _, rest = path.partition("/")
    parts = rest.split("/")
    # A member must sit inside a declared tree, at least one part and at most MAX_DEPTH down.
    if tree not in trees or not rest or len(parts) > MAX_DEPTH:
        raise BackupError(f"Backup member {path!r} is outside the archive's declared layout.")
    for part in parts:
        if PATH_PART.fullmatch(part) is None:
            raise BackupError(f"Backup member {path!r} has a path part a backup never writes.")
