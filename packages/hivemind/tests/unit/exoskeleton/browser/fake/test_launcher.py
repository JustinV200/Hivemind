"""Unit tests for hivemind.exoskeleton.browser.fake.launcher: a real launch's shape, faked."""

from __future__ import annotations

from pathlib import Path

import pytest

from hivemind.cell.fake import FakeSession, FakeStart
from hivemind.exoskeleton.browser import BrowserLaunch
from hivemind.exoskeleton.browser.fake import (
    FAKE_BROWSER_PROGRAM,
    FAKE_LOG_FILE,
    FakeBrowser,
    FakeBrowserLauncher,
    login_site,
)
from hivemind.exoskeleton.errors import AttachError
from hivemind.exoskeleton.scratch import ScratchLayout
from waggle.clock import FakeClock


def _request(scratch: Path) -> BrowserLaunch:
    layout = ScratchLayout.under(scratch)
    return BrowserLaunch(layout=layout, headed=True, sandbox=False, environment={"HOME": "/h"})


async def test_launch_starts_a_stand_in_in_scratch_and_returns_a_fake_browser(
    tmp_path: Path,
) -> None:
    session = FakeSession(tmp_path, FakeClock())
    launcher = FakeBrowserLauncher(login_site(), FakeClock())
    request = _request(tmp_path)

    launched = await launcher.launch(session, request)

    browser_dir = request.layout.browser_dir
    assert isinstance(launched.browser, FakeBrowser)
    assert await launched.browser.url() == "about:blank"
    assert launcher.launches == (request,)
    (spec,) = session.started
    assert spec.argv[0] == FAKE_BROWSER_PROGRAM
    assert spec.log_path == browser_dir / FAKE_LOG_FILE
    assert spec.env == {"HOME": "/h"}
    assert session.running_pids == tuple(process.pid for process in launched.processes)


async def test_a_launcher_that_is_not_ready_stops_its_process_and_raises(tmp_path: Path) -> None:
    session = FakeSession(tmp_path, FakeClock())
    launcher = FakeBrowserLauncher(login_site(), FakeClock(), ready=False)

    with pytest.raises(AttachError, match="never became ready"):
        await launcher.launch(session, _request(tmp_path))

    assert len(session.started) == 1
    assert session.running_pids == ()


async def test_a_start_the_session_refuses_is_an_attach_error(tmp_path: Path) -> None:
    session = FakeSession(tmp_path, FakeClock())
    session.script_start(FAKE_BROWSER_PROGRAM, FakeStart(fails="No such file or directory"))

    with pytest.raises(AttachError, match="could not start"):
        await FakeBrowserLauncher(login_site(), FakeClock()).launch(session, _request(tmp_path))
