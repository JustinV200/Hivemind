"""Unit tests for hivemind.exoskeleton.browser.playwright.hosts.peers: WebRTC is switched off."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

pytest.importorskip("playwright.async_api")

from hivemind.exoskeleton.browser.playwright.hosts.peers import (
    PEER_CONNECTIONS_OFF,
    PEER_GLOBALS,
    block_peer_connections,
)


@dataclass
class _Context:
    scripts: list[str] = field(default_factory=list)

    async def add_init_script(self, script: str) -> None:
        self.scripts.append(script)


def test_the_script_deletes_every_peer_constructor_and_never_throws() -> None:
    for name in PEER_GLOBALS:
        assert f'"{name}"' in PEER_CONNECTIONS_OFF
    assert "delete globalThis[name]" in PEER_CONNECTIONS_OFF
    assert "try" in PEER_CONNECTIONS_OFF and "catch" in PEER_CONNECTIONS_OFF


async def test_the_script_is_added_to_every_page_of_the_context() -> None:
    context = _Context()

    await block_peer_connections(context)  # type: ignore[arg-type, unused-ignore]

    assert context.scripts == [PEER_CONNECTIONS_OFF]
