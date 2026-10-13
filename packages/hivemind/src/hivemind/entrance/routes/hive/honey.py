"""Serve the honey resource: the Hive's knowledge as a folder tree, read-only, filtered per viewer.

The Observation Hive's Honey browser (roadmap step 12.9, codingrules 8.11) reads three routes:
``GET /v1/honey/tree`` lists a folder (``/``, ``/hive``, ``/cells/<id>`` with that Cell's live wax
under ``/cells/<id>/wax``, ``/bees/<id>``, ``/tasks/<id>``, ``/bee-bread``), ``GET
/v1/honey/items/{item_id}`` reads one item with its text, and ``GET /v1/honey/search`` searches a
folder. All need ``observe``. A folder's scope is the ``observe:honey:<scope>`` the device must
also hold (``hive``, ``cell:<id>``, ``bee:<id>`` or ``task:<id>``; the root and the index folders
have none of their own and list only what the device may read), and an item is read only within
the device's clearance allowance (``honey:clearance:c2`` for a ``C2`` item, ``C1`` otherwise). A
device that lacks a folder's scope is refused (403) and never sees the folder listed in its
parent; an item it may not read is refused the same way and never named. Nothing here writes: the
human's note goes to the Queen through the chat, the Entrance's one write (codingrules 8.11).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes.hive``.
    Registered in the route table. Calls into ``hivemind.entrance.reads`` (the viewer and the
    Honey browser's reads) and the gate's capability check.

Key invariants:
    - Read only: no route here changes Honey, and the Honey reads the Entrance holds cannot
      propose a note.
    - Nothing reaches a client outside the device's scopes or above its allowance: the browser
      filters by the viewer's reader, and the item route checks again at the gate.
    - A refused item answers one fixed capability (``observe:honey``), never its scope or label.

See Also:
    - hivemind.entrance.reads.honey for how a device becomes a reader.
    - hivemind.honey_store.browse for the tree and its visibility rules.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Path, Query

from hivemind.entrance.gate.admit import Caller, authorise
from hivemind.entrance.gate.errors import CapabilityDeniedError
from hivemind.entrance.gate.params import CallerParam, Services
from hivemind.entrance.gate.services import EntranceServices
from hivemind.entrance.gate.spec import BOTH_LISTENERS, RouteEffect, RouteSpec, session_with
from hivemind.entrance.reads import (
    honey_viewer,
    locate_folder,
    locate_item,
    read_folder,
    read_item,
    read_search,
    require_honey,
    scope_of,
)
from hivemind.guard import CapabilityFamily
from hivemind.honey_store.browse import BrowsePath
from hivemind.observation import (
    HoneyFolderView,
    HoneyItemView,
    HoneySearchPage,
    HoneySearchQuery,
    HoneyTreeQuery,
)

OBSERVE = "observe"  # Every Honey read needs it; the folder's scope and the label come on top.
OBSERVE_HONEY = CapabilityFamily.OBSERVE_HONEY.value  # `observe:honey`, then `:<scope>`.
CLEARANCE = CapabilityFamily.HONEY_CLEARANCE.value  # `honey:clearance`, then `:c2`.
ITEM_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"  # An id: a Honey row's, a wax note's, an entry's.

ItemIdPath = Annotated[str, Path(pattern=ITEM_ID_PATTERN, description="The item's id.")]

__all__ = ["ROUTES"]


async def get_tree(
    caller: CallerParam, services: Services, query: Annotated[HoneyTreeQuery, Query()]
) -> HoneyFolderView:
    """List one folder of the Honey tree as the caller may see it.

    Args:
        caller: The admitted caller (their capabilities decide what they see).
        services: The Entrance's services.
        query: The folder, where to continue from and how many.

    Returns:
        The folder: sub-folders, a page of items, the cursor for the next page.

    Raises:
        HoneyFolderNotFoundError: The path names no folder of the tree (404).
        CapabilityDeniedError: The folder's scope is not one the device may observe (403).
    """
    honey = require_honey(services.hive)
    target = locate_folder(query.path)
    await _authorise_folder(services, caller, target)
    viewer = honey_viewer(caller.device.id, caller.session.capabilities)
    # Latency: local SQLite reads for the folder's page, and memory's for wax and Bee Bread.
    return await read_folder(honey, viewer, target, query)


async def get_item(item_id: ItemIdPath, caller: CallerParam, services: Services) -> HoneyItemView:
    """Read one item, with its text, within the caller's scopes and clearance.

    Args:
        item_id: The item's id: a Honey row's, a wax note's or a Bee Bread entry's.
        caller: The admitted caller.
        services: The Entrance's services.

    Returns:
        The item and its provenance and label.

    Raises:
        HoneyItemNotFoundError: No live item has this id (404).
        CapabilityDeniedError: The item's scope or label is beyond the device (403, naming
            only ``observe:honey``).
    """
    honey = require_honey(services.hive)
    viewer = honey_viewer(caller.device.id, caller.session.capabilities)
    # Read once with no limits just to learn the item's scope and label; the answer is built from
    # the second read, through the viewer's own reader.
    scope, label = scope_of(await locate_item(honey, caller.device.id, item_id))
    try:
        await authorise(services, caller, f"{OBSERVE_HONEY}:{scope}")
        # Anything above the `observe` baseline needs the rung it carries.
        if label.rank > viewer.ceiling.rank:
            await authorise(services, caller, f"{CLEARANCE}:{label.value.lower()}")
    except CapabilityDeniedError:
        # The refusal is already on the trail with the real capability; the client is told only
        # the family, so it learns nothing about the scope or label of an item it may not read.
        raise CapabilityDeniedError(OBSERVE_HONEY) from None
    # Latency: one local read through the viewer's reader, which filters again.
    return await read_item(honey, viewer, item_id)


async def get_search(
    caller: CallerParam, services: Services, query: Annotated[HoneySearchQuery, Query()]
) -> HoneySearchPage:
    """Search one folder of the Honey tree, ranked, within the caller's scopes and clearance.

    Args:
        caller: The admitted caller.
        services: The Entrance's services.
        query: The folder, the words, where to continue from and how many.

    Returns:
        One page of hits: score, snippet, provenance and label; none outside the caller's reach.

    Raises:
        HoneyFolderNotFoundError: The path names no folder that holds Honey (404).
        CapabilityDeniedError: The folder's scope is not one the device may observe (403).
    """
    honey = require_honey(services.hive)
    target = locate_folder(query.path)
    await _authorise_folder(services, caller, target)
    viewer = honey_viewer(caller.device.id, caller.session.capabilities)
    # Latency: the retriever's full-text and vector search, one embedding call at most.
    return await read_search(honey, viewer, target, query)


async def _authorise_folder(services: EntranceServices, caller: Caller, target: BrowsePath) -> None:
    """Hold the caller to the folder's scope at the Entrance route point; none for an index."""
    # The root, the index folders and /bee-bread have no scope of their own: their lists are cut
    # to the scopes the viewer reaches instead.
    if target.scope is not None:
        await authorise(services, caller, f"{OBSERVE_HONEY}:{target.scope}")


ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec(
        method="GET",
        path="/v1/honey/tree",
        listeners=BOTH_LISTENERS,
        access=session_with(OBSERVE),
        effect=RouteEffect.READ,
        endpoint=get_tree,
        summary="List one folder of the Honey tree: observe:honey:<scope> for the folder's "
        "scope, items within the caller's clearance.",
        response_model=HoneyFolderView,
    ),
    RouteSpec(
        method="GET",
        path="/v1/honey/items/{item_id}",
        listeners=BOTH_LISTENERS,
        access=session_with(OBSERVE),
        effect=RouteEffect.READ,
        endpoint=get_item,
        summary="Read one Honey item with its text: observe:honey:<scope> for the item's scope, "
        "and honey:clearance:c2 for a C2 item.",
        response_model=HoneyItemView,
    ),
    RouteSpec(
        method="GET",
        path="/v1/honey/search",
        listeners=BOTH_LISTENERS,
        access=session_with(OBSERVE),
        effect=RouteEffect.READ,
        endpoint=get_search,
        summary="Search one folder of the Honey tree: observe:honey:<scope> for the folder's "
        "scope, hits within the caller's clearance.",
        response_model=HoneySearchPage,
    ),
)
