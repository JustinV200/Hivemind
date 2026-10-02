"""Unit tests for hivemind.exoskeleton.browser.playwright.chrome: DevTools shut, downloads denied.

`ChromeGuard` is driven with stand-ins for Playwright's browser and its CDP session, the two
objects it touches. That a real Chromium reports a DevTools window as a target, and closes it on
`Target.closeTarget`, was checked on Edge 154 on Windows (2026-10-02; ADR-0059).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

pytest.importorskip("playwright.async_api")

from playwright.async_api import Error as PlaywrightError

from hivemind.exoskeleton.browser.hosts import BlockRule, BrowserBlock
from hivemind.exoskeleton.browser.playwright.chrome import ChromeGuard, is_devtools

_DEVTOOLS = "devtools://devtools/bundled/devtools_app.html?remoteBase=x"


@dataclass
class _Session:
    """A CDP session that records every command and keeps every event handler."""

    fails_close: bool = False
    sent: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    handlers: dict[str, Callable[..., Any]] = field(default_factory=dict)

    def on(self, event: str, handler: Callable[..., Any]) -> None:
        self.handlers[event] = handler

    async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if method == "Target.closeTarget" and self.fails_close:
            raise PlaywrightError("No target with given id found")
        self.sent.append((method, params or {}))
        return {}


@dataclass
class _Browser:
    session: _Session

    async def new_browser_cdp_session(self) -> _Session:
        return self.session


@dataclass
class _Sink:
    blocks: list[BrowserBlock] = field(default_factory=list)

    async def __call__(self, block: BrowserBlock) -> None:
        self.blocks.append(block)


def _event(target_id: str, url: str) -> dict[str, Any]:
    return {"targetInfo": {"targetId": target_id, "url": url, "type": "other"}}


async def _installed(session: _Session, sink: _Sink | None = None) -> ChromeGuard:
    guard = ChromeGuard(sink)
    await guard.install(_Browser(session))  # type: ignore[arg-type, unused-ignore]
    return guard


async def test_install_watches_every_target_and_denies_downloads() -> None:
    session = _Session()

    await _installed(session)

    assert set(session.handlers) == {"Target.targetCreated", "Target.targetInfoChanged"}
    assert session.sent == [
        ("Target.setDiscoverTargets", {"discover": True}),
        ("Browser.setDownloadBehavior", {"behavior": "deny"}),
    ]


async def test_a_devtools_window_is_closed_once_and_reported_once() -> None:
    session, sink = _Session(), _Sink()
    guard = await _installed(session, sink)

    await guard.seen(_event("T1", _DEVTOOLS))
    await guard.seen(_event("T1", _DEVTOOLS))  # Created, then changed: the same window.

    closes = [params for method, params in session.sent if method == "Target.closeTarget"]
    assert closes == [{"targetId": "T1"}]
    assert sink.blocks == [BrowserBlock(BlockRule.DEVTOOLS)]


async def test_a_page_is_left_alone() -> None:
    session, sink = _Session(), _Sink()
    guard = await _installed(session, sink)

    await guard.seen(_event("P1", "https://example.com/devtools://"))
    await guard.seen({"targetInfo": {"targetId": 7, "url": _DEVTOOLS}})  # Malformed: ignored.
    await guard.seen({})

    assert all(method != "Target.closeTarget" for method, _ in session.sent)
    assert sink.blocks == []


async def test_a_window_already_gone_is_still_reported_and_never_raises() -> None:
    session, sink = _Session(fails_close=True), _Sink()
    guard = await _installed(session, sink)

    await guard.seen(_event("T2", "chrome-devtools://devtools/x"))

    assert sink.blocks == [BrowserBlock(BlockRule.DEVTOOLS)]


@pytest.mark.parametrize(
    ("url", "devtools"),
    [
        (_DEVTOOLS, True),
        ("DevTools://devtools/x", True),
        ("chrome-devtools://devtools/x", True),
        ("about:blank", False),
        ("https://devtools.example/", False),
    ],
)
def test_is_devtools_knows_a_devtools_front_end_by_its_scheme(url: str, devtools: bool) -> None:
    assert is_devtools(url) is devtools
