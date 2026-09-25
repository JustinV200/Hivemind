"""Provide ``hive keys list|create|revoke``: the Hive's Waggle-side Ed25519 keys, by name.

The Hive keeps the Ed25519 keys its Waggle-side principals sign with (Waggle is the Hive's
bee-to-bee wire protocol) in its secret store, ``[hive] secrets_dir``. ``list`` shows each key's
name, what it is, its public key in hex (what a peer pins) and its fingerprint (what two people
compare), never a private key. ``create NAME`` mints a named node key for a peer to pin. ``revoke
NAME`` deletes a node key's private half after a confirmation, and says plainly what that does and
does not do: nothing on this Hive Stand can sign as it again, but a peer that pinned its public
key trusts it until that pin is removed. The Hive's identity key and the console's key are
refused (``hivemind.cli.keys.ring`` says why). Each command is a thin layer over ``ring``.

Fits into the Hive:
    Layer 7 (the terminal), inside ``hivemind.cli.keys``. ``app`` is registered on the root
    ``hive`` application by ``hivemind.cli.app``. Calls into ``hivemind.cli.keys.ring``,
    ``hivemind.cli.stores`` (the manifest) and ``hivemind.common.secrets``.

Key invariants:
    - No private key material is printed.
    - Every refusal is one stderr line and exit 1.

See Also:
    - hivemind.cli.keys.ring for the rules.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import asdict
from typing import Annotated, NoReturn

import typer

from hivemind.cli.keys.ring import KeyEntry, create_key, list_keys, revoke_key
from hivemind.cli.landing import describe
from hivemind.cli.stores import DEFAULT_MANIFEST, JsonOption, ManifestOption, load_manifest_or_exit
from hivemind.common.errors import HiveMindError
from hivemind.common.secrets import FileSecretStore
from hivemind.manifest import HiveManifest

PINNED_WARNING = (
    "A peer that pinned this public key (a Swarm node's trusted keys, a manifest) still trusts "
    "it until that pin is removed: Waggle has no revocation list in this phase."
)

app = typer.Typer(
    name="keys",
    help="The Hive's Waggle-side Ed25519 keys: list them, mint a named one, revoke one.",
    no_args_is_help=True,
)

__all__ = ["PINNED_WARNING", "app"]

NameArgument = Annotated[str, typer.Argument(help="The key's name, e.g. relay (relay.ed25519).")]


@app.command("list")
def list_command(manifest: ManifestOption = DEFAULT_MANIFEST, as_json: JsonOption = False) -> None:
    """List every Ed25519 key in the secret store: name, role, public key, fingerprint."""
    store = _store(load_manifest_or_exit(manifest))
    entries = _run("list", lambda: asyncio.run(list_keys(store)))
    if as_json:
        typer.echo(json.dumps({"keys": [asdict(entry) for entry in entries]}, indent=2))
        return
    if not entries:
        typer.echo("No Ed25519 key in the secret store yet (hive serve mints the Hive's).")
    for entry in entries:
        typer.echo(_row(entry))


@app.command("create")
def create_command(name: NameArgument, manifest: ManifestOption = DEFAULT_MANIFEST) -> None:
    """Mint a named node key; print its public key for the peers that should trust it."""
    store = _store(load_manifest_or_exit(manifest))
    entry = _run("create", lambda: asyncio.run(create_key(store, name)))
    typer.echo(f"Created node key {entry.name} in the Hive's secret store.")
    typer.echo(f"  public key: {entry.public_key_hex}  (pin this on the peers that trust it)")
    typer.echo(f"  fingerprint: {entry.fingerprint}")


@app.command("revoke")
def revoke_command(
    name: NameArgument,
    manifest: ManifestOption = DEFAULT_MANIFEST,
    yes: Annotated[bool, typer.Option("--yes", help="Do not ask for confirmation.")] = False,
) -> None:
    """Delete a node key's private half, so nothing here can sign as it again."""
    store = _store(load_manifest_or_exit(manifest))
    if not yes and not typer.confirm(f"Revoke the key {name}? Its private half is deleted."):
        raise typer.Exit(code=1)
    entry = _run("revoke", lambda: asyncio.run(revoke_key(store, name)))
    typer.echo(f"Revoked {entry.name} (fingerprint {entry.fingerprint}): its private half is")
    typer.echo("  deleted, so nothing on this Hive Stand can sign as it again.")
    typer.echo(PINNED_WARNING)


def _store(manifest: HiveManifest) -> FileSecretStore:
    """The Hive's secret store, as the manifest names it."""
    return FileSecretStore(manifest.resolve_path(manifest.hive.secrets_dir))


def _run[T](verb: str, work: Callable[[], T]) -> T:
    """Run the work; a refusal is one stderr line and exit 1."""
    try:
        return work()
    except (HiveMindError, OSError) as exc:
        # Typed failures only: a key refused on purpose, or the secret store's own files.
        _refuse(verb, exc)


def _refuse(verb: str, exc: Exception) -> NoReturn:
    """Print why ``hive keys <verb>`` could not act, and exit 1."""
    typer.echo(f"hive keys {verb} refused: {describe(exc)}", err=True)
    raise typer.Exit(code=1) from exc


def _row(entry: KeyEntry) -> str:
    """One key as a line: name, role, public key and fingerprint when readable."""
    public = entry.public_key_hex or "-"
    return f"{entry.name:<24} {entry.role:<16} {public}  {entry.fingerprint or '-'}"
