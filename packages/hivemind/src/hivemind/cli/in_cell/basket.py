"""Build the in-Cell Warden's own resident Basket: a store under its Cell's scratch.

A Virtual Cell keeps the blobs its Warden chose (a tool package, a wheel, a dataset) in a resident
set of its own (ADR-0048). It has no manifest and no Hive database, so this module opens a small
SQLite file and a byte tree under the Cell's scratch root (``<scratch>/basket/basket.sqlite`` and
``<scratch>/basket/blobs``), the same volume Overwintering keeps, so the resident set survives a
dormant Cell. The ceiling is the grant's ``Ceilings.resident_basket_disk_bytes`` when that is
non-zero (0 means "no ceiling beyond free disk" on the wire, which a ResidentBasket cannot
express: a 0 ceiling admits nothing), else the ``[basket]`` default the manifest schema documents.
No grant has arrived when the Warden is composed, so ``build_in_cell_warden_deps`` passes no
Ceilings and gets the default; the Warden's Basket tick (a later step) rebuilds the view with a
grant's figure through ``resident_ceiling_bytes``. A Cell whose scratch cannot be written gets no
resident Basket and says so, rather than failing to start.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.cli.in_cell``. Called by
    ``hivemind.cli.in_cell.deps.build_in_cell_warden_deps``. Calls into ``hivemind.basket``,
    ``hivemind.common.sqlite``, ``hivemind.forage`` (Ceilings), ``hivemind.manifest.schema``
    (the documented default) and waggle only.

Key invariants:
    - The ceiling returned is never 0.
    - The store is opened synchronously (the migration and the constructor are both sync), so the
      composition stays a plain function; no thread starts until the first call on the store.
    - Failure to open degrades to None with one warning, never an exception.

See Also:
    - docs/adr/0048-basket-inert-blobs-residency-and-transfer.md for residency.
    - hivemind.cli.in_cell.deps, the one caller.
"""

from __future__ import annotations

import sqlite3

from hivemind.basket import BasketStore, ResidentBasket, SqliteBasketStore, apply_basket_migrations
from hivemind.cli.in_cell.config import InCellRuntimeConfig
from hivemind.common.logging import get_logger
from hivemind.common.sqlite import connect
from hivemind.forage import Ceilings
from hivemind.manifest.schema.royal_jelly import DEFAULT_RESIDENT_CEILING_BYTES
from waggle.clock import Clock

BASKET_DIR = "basket"  # Under the Cell's scratch root: the volume Overwintering keeps.
DATABASE_FILE = "basket.sqlite"  # Metadata: blobs, residency rows, shipments.
BLOBS_DIR = "blobs"  # Bytes, laid out `<sha[:2]>/<sha>` by the store.

log = get_logger(__name__)

__all__ = ["build_basket_parts", "build_resident_basket", "resident_ceiling_bytes"]


def resident_ceiling_bytes(
    ceilings: Ceilings | None, default: int = DEFAULT_RESIDENT_CEILING_BYTES
) -> int:
    """Return the resident Basket ceiling: the grant's figure when non-zero, else ``default``.

    Args:
        ceilings: The Queen's Forage ceilings for this Cell, once a grant has set them; None before.
        default: The ``[basket] default_resident_ceiling_bytes`` to fall back on.

    Returns:
        A positive byte count.
    """
    # 0 on the wire means "no ceiling beyond free disk"; a ResidentBasket admits nothing at 0, so
    # the documented default stands in for it.
    granted = ceilings.resident_basket_disk_bytes if ceilings is not None else 0
    return granted if granted > 0 else default


def build_resident_basket(config: InCellRuntimeConfig, clock: Clock) -> ResidentBasket | None:
    """Open this Cell's resident Basket below its scratch root, or None when it cannot be opened.

    Args:
        config: This process's validated runtime config; its scratch root and Cell id.
        clock: Stamps residency rows and migrations.

    Returns:
        A ResidentBasket over a store at ``<scratch>/basket``; None (logged once) when the scratch
        root cannot be written or the database cannot be opened.
    """
    parts = build_basket_parts(config, clock)
    return parts[0] if parts is not None else None


def build_basket_parts(
    config: InCellRuntimeConfig, clock: Clock
) -> tuple[ResidentBasket, BasketStore] | None:
    """Open this Cell's store and its resident view: what its Warden receives and deposits into.

    The Warden writes received and retained blobs to the store itself (roadmap phase 9), so it
    needs the store as well as the resident view over it.

    Args:
        config: This process's validated runtime config; its scratch root and Cell id.
        clock: Stamps residency rows and migrations.

    Returns:
        The resident Basket and the store behind it; None (logged once) when the scratch root
        cannot be written or the database cannot be opened.
    """
    spawn = config.spawn_config
    root = spawn.scratch_root / BASKET_DIR
    try:
        root.mkdir(parents=True, exist_ok=True)
        connection = connect(root / DATABASE_FILE)
        # Blocking but brief: one migration transaction on a fresh file, before any loop work.
        apply_basket_migrations(connection, clock)
    except (OSError, sqlite3.Error) as exc:
        log.warning("in_cell.resident_basket_unavailable", reason=f"{type(exc).__name__}: {exc}")
        return None
    store = SqliteBasketStore(connection, root / BLOBS_DIR, clock)
    return ResidentBasket(store, spawn.cell_id, resident_ceiling_bytes(None), clock), store
