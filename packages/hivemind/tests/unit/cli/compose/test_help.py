"""Test hivemind.cli.compose.help: the Ask is built from the served build's pages, or not at all.

Over a fake manifest, `serve_hive` wires the Ask when the Observation Hive's build directory
holds help pages: the retriever is the EMBEDDER slot's (the fake kind embeds) over keywords, the
answerer runs on a chat binding bound through the Hive's own registry and calls through its
Fanner's lane, and the route is mounted. Without the pages there is no Ask.

Fits into the Hive:
    Mirrors src/hivemind/cli/compose/help.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from builders.cli import fake_manifest
from builders.entrance.help import PAGES

from hivemind.cli.compose.entrance import (
    OBSERVATION_BUILD,
    ServedHive,
    build_served_hive,
    serve_hive,
)
from hivemind.cli.compose.help import HELP_PAGES_DIR
from hivemind.entrance.help import HelpServices
from hivemind.entrance.help.retrieval import EmbeddingRetriever
from hivemind.llm import FannerLane
from hivemind.llm.routing import CHAT_SLOTS
from hivemind.manifest import load_manifest
from waggle.clock import SystemClock

_LOOPBACK = 'bind = "127.0.0.1:0"\n'  # The Ask needs no remote listener to be wired.


def _served(tmp_path: Path, *, pages: bool) -> ServedHive:
    """Compose `hive serve`'s Hive over a fake manifest, with or without a build's help pages."""
    path = fake_manifest(tmp_path)
    path.write_text(path.read_text(encoding="utf-8") + f"\n[entrance]\n{_LOOPBACK}", "utf-8")
    if pages:
        # The build lives beside the manifest, as OBSERVATION_BUILD is manifest-relative.
        build = tmp_path / OBSERVATION_BUILD / HELP_PAGES_DIR
        build.mkdir(parents=True)
        for page, text in PAGES.items():
            (build / f"{page}.md").write_text(text, encoding="utf-8")
    return build_served_hive(load_manifest(path, {}), environ={}, clock=SystemClock())


async def _help(served: ServedHive) -> HelpServices | None:
    """Enter serve_hive and return the help the Entrance was built with."""
    async with serve_hive(served) as entrance:
        return entrance.services.help


def test_the_ask_is_wired_over_the_builds_pages_the_embedder_and_a_chat_binding(
    tmp_path: Path,
) -> None:
    served = _served(tmp_path, pages=True)

    help_ = asyncio.run(_help(served))

    assert help_ is not None
    assert isinstance(help_.retriever, EmbeddingRetriever)
    assert help_.answerer is not None and help_.answerer.slot in CHAT_SLOTS
    # Metered by the Hive's own Fanner, so every answer is one priced llm.call on its trail.
    assert isinstance(help_.answerer.gate, FannerLane)


def test_without_help_pages_there_is_no_ask(tmp_path: Path) -> None:
    assert asyncio.run(_help(_served(tmp_path, pages=False))) is None
