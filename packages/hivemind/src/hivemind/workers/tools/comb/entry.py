"""Define CombToolEntry, PackageSource and CombToolBundle: the Comb tools a Worker is handed.

The Comb Registry (the catalogue of promoted tools) is the source of truth for which tools exist,
but a Worker never reads it (README, core concept 10): its Warden hands it a bundle, attenuated
from the Queen's grant, and the bundle is a list of `CombToolEntry` values the Warden built from
registry rows. An entry carries everything the Worker needs to offer and run one tool (name,
description, schema, needed capabilities, platforms, and the package's SHA-256), and nothing that
could run by itself: a package's bytes come from a `PackageSource` (the resident Basket) and are
verified against the entry's hash before any code loads. `CombToolBundle` is the small mutable
holder the Warden swaps entries in: the one thing a Warden may change under a running Worker.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.tools.comb`. Built by the Warden's
    spawn and its tool relay (wave 3H); read by `hivemind.workers.tools.registry.build_registry`
    and `hivemind.workers.tools.comb.invoke`. Calls into `hivemind.royal_jelly.registry` (the row
    an entry is built from), `hivemind.royal_jelly.spec` (the spec inside it) and waggle only.

Key invariants:
    - An entry names a package by hash and carries no bytes; there is no way to run a hash that no
      entry names.
    - `CombToolBundle` is the only mutable object here (codingrules 8.5): `replace` swaps the whole
      tuple and bumps `version` only when the entries differ, so a no-op never costs a rebuild.
    - `CombToolBundle.loader` owns the per-Worker import cache, because the bundle outlives each
      attempt's registry; a swapped-out entry's module is simply never called again.

See Also:
    - docs/adr/0046 (tool spec, Comb Registry and scopes) and docs/adr/0048 (inert blobs).
    - hivemind.workers.tools.comb.invoke for the runner that loads and runs an entry.
    - hivemind.basket for the resident Basket that satisfies `PackageSource`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from hivemind.royal_jelly.registry import CombToolRow
from hivemind.workers.tools.comb.loader import PackageLoader
from waggle.ids import CellId, ToolId
from waggle.messages.labels import OsFamily
from waggle.messages.tool.authoring import ToolPromoted, ToolScope

# The one entry point every package this phase produces uses (module `tool`, attribute `run`);
# the same value as the spec's own default, restated because the spec package keeps it private.
DEFAULT_ENTRY_POINT = "tool:run"

__all__ = ["DEFAULT_ENTRY_POINT", "CombToolBundle", "CombToolEntry", "PackageSource"]


@dataclass(frozen=True, slots=True)
class CombToolEntry:
    """One promoted tool as a Worker sees it: its description, its needs and its package's hash."""

    name: str  # The tool's name; the capability tool:<name> and the model-facing tool name.
    version: str  # The immutable version this entry names.
    tool_id: ToolId  # Names the tool on the trail.
    description: str  # Model-facing text; untrusted-but-registered, shown as the tool description.
    input_schema_json: str  # JSON Schema text for the arguments; re-validated before every call.
    required_capabilities: tuple[str, ...]  # What the Worker must hold to call it.
    supported_os: tuple[OsFamily, ...]  # Platforms it was written and proved for.
    package_sha256: str  # The hash that names the package; verified before anything loads.
    package_size_bytes: int  # The package's size, for the source to bound its read.
    scope: ToolScope  # HIVE (everywhere) or CELL (one Cell's bees).
    cell_id: CellId | None  # The one Cell a CELL-scope tool is for.
    entry_point: str = DEFAULT_ENTRY_POINT  # `module:attribute` of the async callable.

    @classmethod
    def from_row(cls, row: CombToolRow) -> CombToolEntry:
        """Build an entry from a Comb Registry row: the Warden's one way to make one.

        Args:
            row: A PROMOTED row the Warden read (or was told about) from the registry.

        Returns:
            The entry; its description, schema and needs come from the row's stored spec.
        """
        spec = row.spec
        return cls(
            name=row.name,
            version=row.version,
            tool_id=row.tool_id,
            description=spec.description,
            input_schema_json=spec.input_schema_json,
            required_capabilities=spec.required_capabilities,
            supported_os=spec.supported_os,
            package_sha256=row.package_sha256,
            package_size_bytes=row.package_size,
            scope=row.scope,
            cell_id=row.cell_id,
            entry_point=spec.entry_point,
        )

    @classmethod
    def from_promoted(
        cls, promoted: ToolPromoted, entry_point: str = DEFAULT_ENTRY_POINT
    ) -> CombToolEntry:
        """Build an entry from a `tool.promoted` announcement, for a Warden that has no row.

        Args:
            promoted: The announcement a remote Warden received from the Queen.
            entry_point: The spec's entry point; the announcement does not carry it, and every
                package this phase produces uses the default.

        Returns:
            The entry the announcement describes.
        """
        return cls(
            name=promoted.name,
            version=promoted.version,
            tool_id=promoted.tool_id,
            description=promoted.description,
            input_schema_json=promoted.input_schema_json,
            required_capabilities=promoted.required_capabilities,
            supported_os=promoted.supported_os,
            package_sha256=promoted.package_sha256,
            package_size_bytes=promoted.package_size_bytes,
            scope=promoted.scope,
            cell_id=promoted.cell_id,
            entry_point=entry_point,
        )


class PackageSource(Protocol):
    """Hand over the bytes of a package by hash; the resident Basket satisfies it."""

    async def bytes_for(self, sha256: str) -> bytes:
        """Return the package bytes named by `sha256`.

        Args:
            sha256: The hash an entry names.

        Returns:
            The bytes. The caller verifies their hash; a source need not be trusted.

        Raises:
            hivemind.workers.tools.errors.PackageUnavailableError: The source holds no such blob,
                or the blob it holds does not match its hash.
        """
        ...


class CombToolBundle:
    """The Comb tools one Worker may be offered, and the one thing its Warden may change under it.

    Owns its state in place (codingrules 8.5): `_entries` and `version`. Only the Warden calls
    `replace`, as an intervention (roadmap 9.7); a Worker only reads. Everything runs on one event
    loop, so no lock is needed.
    """

    def __init__(
        self, entries: Iterable[CombToolEntry] = (), loader: PackageLoader | None = None
    ) -> None:
        """Build a bundle over `entries`.

        Args:
            entries: The Comb tools the Worker starts with (usually none).
            loader: The per-Worker import cache; a fresh one when omitted.
        """
        self._entries = tuple(entries)
        self._version = 0
        self.loader = loader if loader is not None else PackageLoader()

    @property
    def version(self) -> int:
        """How many times `replace` changed the entries; a registry built earlier is stale."""
        return self._version

    def entries(self) -> tuple[CombToolEntry, ...]:
        """Return the current entries, in the order the Warden gave them."""
        return self._entries

    def replace(self, entries: Iterable[CombToolEntry]) -> None:
        """Swap in a new set of entries, bumping `version` when it differs from the current one.

        Args:
            entries: The Worker's whole new bundle (not a delta); the Warden re-attenuated it.
        """
        updated = tuple(entries)
        # Equal entries are a no-op: a bump would make the running attempt rebuild its registry
        # (and restart its tool loop from state) for nothing.
        if updated == self._entries:
            return
        self._entries = updated
        self._version += 1
