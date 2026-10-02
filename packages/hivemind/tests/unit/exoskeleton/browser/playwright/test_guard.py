"""Unit tests for hivemind.exoskeleton.browser.playwright.guard: no file outside the roots loads.

`FileGuard.allows` is checked on its own over a real directory tree, symlinks and hard links
included (ADR-0059); the rest runs a real Chromium through the contract suite's harness, whose
file roots are its scratch and the fixture site: a page the bee wrote into scratch that frames and
links a file outside it, and a symlink planted in scratch that points out of it. Nothing outside
may ever reach a read.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

pytest.importorskip("playwright.async_api")

from contracts.browser_harness import OpenBrowser, RealBrowserHarness, short_scratch

from hivemind.exoskeleton.browser.hosts import BlockRule, BrowserBlock
from hivemind.exoskeleton.browser.playwright import FileGuard
from hivemind.exoskeleton.browser.playwright.guard import BLOCKED
from hivemind.exoskeleton.errors import PeripheralError
from waggle.messages.capping import ElementTarget

_SECRET = "the-operator's-own-key"  # noqa: S105 -- a marker the outside file holds, never a credential.


@pytest.fixture
def outside() -> Iterator[Path]:
    """A file beside the lease, never inside it, holding _SECRET."""
    directory = short_scratch("hm-out-")
    secret = directory / "id_rsa"
    secret.write_text(_SECRET)
    yield secret
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture
async def real() -> AsyncIterator[OpenBrowser]:
    """A real Chromium whose file roots are its scratch and the fixture site; skipped without."""
    harness = RealBrowserHarness()
    reason = harness.missing()
    if reason is not None:
        pytest.skip(reason)
    opened = await harness.open()
    try:
        yield opened
    finally:
        await harness.close()


def _symlink_or_skip(target: Path, link: Path) -> None:
    """Make `link` point at `target`, or skip the test where the host will not make a symlink.

    Creating a symlink needs a privilege this account may not hold (notably on Windows without
    Developer Mode); the guard's rule a symlink test checks does not depend on the host.
    """
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("this host will not create a symlink for this test")


async def test_allows_a_file_inside_the_roots_and_nothing_else(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    guard = FileGuard((scratch,))
    await guard.resolve()

    assert await guard.allows((scratch / "page.html").as_uri())
    assert not await guard.allows((tmp_path / "elsewhere.txt").as_uri())
    assert not await guard.allows("file://fileserver/share/x")


async def test_a_guard_whose_roots_are_unresolved_allows_nothing(tmp_path: Path) -> None:
    guard = FileGuard((tmp_path,))

    assert not await guard.allows((tmp_path / "page.html").as_uri())


async def test_a_symlink_out_of_the_roots_is_not_allowed(tmp_path: Path) -> None:
    scratch, target = tmp_path / "scratch", tmp_path / "id_rsa"
    scratch.mkdir()
    target.write_text(_SECRET)
    _symlink_or_skip(target, scratch / "planted")
    guard = FileGuard((scratch,))
    await guard.resolve()

    assert not await guard.allows((scratch / "planted").as_uri())


async def test_a_hard_link_into_scratch_is_not_allowed(tmp_path: Path) -> None:
    # Arrange: a second name inside scratch for a file outside it; realpath cannot tell.
    scratch, target = tmp_path / "scratch", tmp_path / "id_rsa"
    scratch.mkdir()
    target.write_text(_SECRET)
    os.link(target, scratch / "linked")
    guard = FileGuard((scratch,))
    await guard.resolve()

    assert not await guard.allows((scratch / "linked").as_uri())


async def test_a_file_with_two_names_inside_scratch_is_not_allowed_either(tmp_path: Path) -> None:
    # Arrange: the guard cannot know where a link's other name is, so any second name refuses.
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "page.html").write_text("<h1>Mine</h1>")
    os.link(scratch / "page.html", scratch / "again.html")
    guard = FileGuard((scratch,))
    await guard.resolve()

    assert not await guard.allows((scratch / "page.html").as_uri())


async def test_a_directory_and_a_missing_file_inside_scratch_are_judged_by_path(
    tmp_path: Path,
) -> None:
    scratch = tmp_path / "scratch"
    (scratch / "site" / "nested").mkdir(parents=True)  # A directory's link count exceeds one.
    guard = FileGuard((scratch,))
    await guard.resolve()

    assert await guard.allows((scratch / "site").as_uri())
    assert await guard.allows((scratch / "missing.html").as_uri())


async def test_a_blocked_file_request_is_reported_without_its_path(tmp_path: Path) -> None:
    blocks: list[BrowserBlock] = []

    async def sink(block: BrowserBlock) -> None:
        blocks.append(block)

    answers: list[str] = []
    guard = FileGuard((tmp_path,), sink)
    await guard.resolve()

    await guard.decide(_FileRoute("file:///etc/passwd", answers))  # type: ignore[arg-type, unused-ignore]

    assert answers == [f"abort:{BLOCKED}"]
    assert blocks == [BrowserBlock(BlockRule.FILE)]


class _FileRoute:
    """A stand-in for Playwright's Route: the URL it asks about, and how it was answered."""

    def __init__(self, url: str, answers: list[str]) -> None:
        self.request = type("Request", (), {"url": url})()
        self._answers = answers

    async def continue_(self) -> None:
        self._answers.append("continue")

    async def abort(self, error_code: str) -> None:
        self._answers.append(f"abort:{error_code}")


@pytest.mark.integration
async def test_a_frame_and_a_link_to_a_file_outside_never_show_it(
    real: OpenBrowser, outside: Path
) -> None:
    # Arrange: the kind of page a bee could write into its own scratch.
    page = real.session.scratch_dir / "escape.html"
    page.write_text(
        f'<html><body><h1>Escape</h1><a href="{outside.as_uri()}">Leak</a>'
        f'<iframe src="{outside.as_uri()}"></iframe></body></html>'
    )
    await real.browser.navigate(page.as_uri())
    framed = await real.browser.snapshot() + await real.browser.page_text()

    await real.browser.click(ElementTarget(role="link", name="Leak"))
    followed = await _settled_text(real)

    assert _SECRET not in framed
    assert _SECRET not in followed
    assert not (await real.browser.url()).startswith("file:")


@pytest.mark.integration
async def test_a_symlink_planted_in_scratch_is_blocked_by_the_route(
    real: OpenBrowser, outside: Path
) -> None:
    # Arrange: inside scratch as written, outside once resolved; only the route can tell.
    planted = real.session.scratch_dir / "planted.txt"
    _symlink_or_skip(outside, planted)

    with pytest.raises(PeripheralError):
        await real.browser.navigate(planted.as_uri())

    assert _SECRET not in await _settled_text(real)


async def _settled_text(opened: OpenBrowser) -> str:
    """Read the page's text, retrying once if a navigation was still landing."""
    try:
        return await opened.browser.page_text()
    except PeripheralError:
        return await opened.browser.page_text()
