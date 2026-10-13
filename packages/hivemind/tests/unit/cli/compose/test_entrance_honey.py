"""Test `hive serve` Honey wiring: the Entrance reads Honey through the Hive's own store and memory.

Roadmap 12.9: the Hive `hive serve` runs hands the Entrance a Honey browser built over the Hive's
own Honey Store and retriever and its memory store (live Cell Wax and Bee Bread), and hands it as
its read side only: the browser it holds is the real one, but the Entrance's type for it has no
method that proposes a note. The test composes the Hive (the fake provider, the Hive's own SQLite
file) and enters ``serve_hive`` on loopback.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from builders.cli import fake_manifest

from hivemind.cell import HoneyClearance
from hivemind.cli.compose.entrance import ServedHive, build_served_hive, serve_hive
from hivemind.entrance.gate import HoneyReads
from hivemind.honey_store.browse import HoneyBrowser, Page, operator_reader
from hivemind.manifest import load_manifest
from waggle.clock import SystemClock

_LOOPBACK = 'bind = "127.0.0.1:0"\n'  # The browser needs no remote listener.


async def _honey(served: ServedHive) -> tuple[HoneyReads | None, list[str], int]:
    """Serve the Hive; list the Honey root and search `/hive` through what the Entrance got."""
    async with serve_hive(served) as entrance:
        honey = entrance.services.hive.honey
        assert honey is not None
        reader = operator_reader(served.hive.manifest.hive.id, HoneyClearance.C2)
        root = await honey.ls("/", reader, Page(limit=10))
        # An empty store finds nothing, but a browser with no retriever would refuse to search.
        found = await honey.search("/hive", "anything at all", reader, 5)
        return honey, [entry.path for entry in root.entries], len(found.hits)


def test_the_entrance_reads_honey_through_a_browser_over_the_hives_own_store(
    tmp_path: Path,
) -> None:
    path = fake_manifest(tmp_path)
    path.write_text(path.read_text(encoding="utf-8") + f"\n[entrance]\n{_LOOPBACK}")
    served = build_served_hive(load_manifest(path, {}), environ={}, clock=SystemClock())

    honey, root, hits = asyncio.run(_honey(served))

    assert isinstance(honey, HoneyBrowser)
    assert root == ["/hive", "/cells", "/bees", "/tasks", "/bee-bread"]
    assert hits == 0
