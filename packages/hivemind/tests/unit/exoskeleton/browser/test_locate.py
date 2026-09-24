"""Unit tests for hivemind.exoskeleton.browser.locate: candidates in order, verified on the Cell."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path, PureWindowsPath

import pytest

from hivemind.cell import CompletedCommand, ExecSpec
from hivemind.cell.fake import FakeSession
from hivemind.exoskeleton.browser.locate import (
    MACOS,
    PATH_NAMES,
    PLAYWRIGHT_BROWSERS_ENV,
    WINDOWS,
    ChromiumLocator,
    HostPlatform,
    browser_candidates,
)
from hivemind.exoskeleton.errors import AttachError
from waggle.clock import FakeClock

_NOT_FOUND = CompletedCommand(exit_code=127, stdout=b"", stderr=b"not found", duration_s=0.0)


class _Cell:
    """A FakeSession answering `--version` (or `where`) from a table, recording every argv."""

    def __init__(
        self, scratch: Path, answers: Mapping[str, str], slow: frozenset[str] = frozenset()
    ) -> None:
        self.calls: list[tuple[str, ...]] = []
        self._answers = answers
        self.session = FakeSession(
            scratch, FakeClock(), responder=self._respond, slow_commands=slow
        )

    def _respond(self, spec: ExecSpec) -> CompletedCommand:
        self.calls.append(spec.argv)
        # `where` names what it looks for last; everything else is the program itself.
        key = spec.argv[-1] if spec.argv[0] == "where" else spec.argv[0]
        if key not in self._answers:
            return _NOT_FOUND
        stdout = self._answers[key].encode()
        return CompletedCommand(exit_code=0, stdout=stdout, stderr=b"", duration_s=0.0)


def _executable(root: Path, *parts: str) -> Path:
    """Create an empty file standing in for a browser executable."""
    path = root.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


def _host(system: str, home: Path, **environment: str) -> HostPlatform:
    return HostPlatform(system=system, environment=environment, home=home)


# ── Candidates ───────────────────────────────────────────────────────────────


def test_playwright_builds_come_first_newest_revision_first(tmp_path: Path) -> None:
    # Arrange: two Chromium revisions in two layouts, plus directories that are not Chromium.
    cache = tmp_path / "browsers"
    older = _executable(cache, "chromium-1100", "chrome-linux", "chrome")
    newer = _executable(cache, "chromium-1200", "chrome-linux64", "chrome")
    _executable(cache, "chromium_headless_shell-1300", "chrome-linux", "headless_shell")
    _executable(cache, "ffmpeg-1011", "ffmpeg-linux")
    host = _host("linux", tmp_path, **{PLAYWRIGHT_BROWSERS_ENV: str(cache)})

    candidates = browser_candidates(host)

    assert candidates == (str(newer), str(older), *PATH_NAMES)


@pytest.mark.parametrize(
    ("system", "cache", "layout", "environment"),
    [
        ("linux", ".cache", ("chrome-linux64", "chrome"), {}),
        ("linux", "xdg", ("chrome-linux", "chrome"), {"XDG_CACHE_HOME": "xdg"}),
        (
            MACOS,
            "Library/Caches",
            ("chrome-mac", "Chromium.app", "Contents", "MacOS", "Chromium"),
            {},
        ),
        (WINDOWS, "AppData/Local", ("chrome-win64", "chrome.exe"), {}),
        (WINDOWS, "local", ("chrome-win", "chrome.exe"), {"LOCALAPPDATA": "local"}),
    ],
)
def test_playwrights_default_cache_is_found_on_every_platform(
    tmp_path: Path,
    system: str,
    cache: str,
    layout: tuple[str, ...],
    environment: dict[str, str],
) -> None:
    root = tmp_path / cache / "ms-playwright"
    executable = _executable(root, "chromium-7", *layout)
    absolute = {name: str(tmp_path / value) for name, value in environment.items()}
    host = HostPlatform(system=system, environment=absolute, home=tmp_path)

    assert browser_candidates(host)[0] == str(executable)


@pytest.mark.parametrize("configured", ["0", "relative/browsers"])
def test_a_browsers_path_that_is_not_an_absolute_directory_is_not_listed(
    tmp_path: Path, configured: str
) -> None:
    _executable(tmp_path, ".cache", "ms-playwright", "chromium-1", "chrome-linux", "chrome")
    host = _host("linux", tmp_path, **{PLAYWRIGHT_BROWSERS_ENV: configured})

    assert browser_candidates(host) == PATH_NAMES


def test_windows_lists_edge_before_chrome_after_the_path_names(tmp_path: Path) -> None:
    host = _host(
        WINDOWS,
        tmp_path,
        **{"PROGRAMFILES(X86)": r"C:\PF86", "ProgramFiles": r"C:\PF", "LOCALAPPDATA": r"C:\U\L"},
    )

    candidates = browser_candidates(host)

    assert candidates[: len(PATH_NAMES)] == PATH_NAMES
    assert candidates[len(PATH_NAMES) :] == (
        r"C:\PF86\Microsoft\Edge\Application\msedge.exe",
        r"C:\PF\Microsoft\Edge\Application\msedge.exe",
        r"C:\PF\Google\Chrome\Application\chrome.exe",
        r"C:\PF86\Google\Chrome\Application\chrome.exe",
        r"C:\U\L\Google\Chrome\Application\chrome.exe",
    )


def test_macos_lists_the_application_bundles(tmp_path: Path) -> None:
    candidates = browser_candidates(_host(MACOS, tmp_path))

    assert "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" in candidates


def test_a_windows_variable_is_found_whatever_its_case(tmp_path: Path) -> None:
    host = _host(WINDOWS, tmp_path, **{"PROGRAMFILES(X86)": r"D:\x86", "Empty": ""})

    assert host.variable("ProgramFiles(x86)") == r"D:\x86"
    assert host.variable("Empty") is None
    assert _host("linux", tmp_path, HOME="/h").variable("home") is None  # Linux keeps case.


def test_host_platform_current_reads_this_host() -> None:
    host = HostPlatform.current()

    assert host.system == sys.platform
    assert host.home == Path.home()


# ── Verification on the Cell ─────────────────────────────────────────────────


async def test_locate_returns_the_first_candidate_that_verifies(tmp_path: Path) -> None:
    # Arrange: the newest build is broken (its --version fails); the older one answers.
    cache = tmp_path / "browsers"
    _executable(cache, "chromium-1200", "chrome-linux64", "chrome")
    older = _executable(cache, "chromium-1100", "chrome-linux", "chrome")
    cell = _Cell(tmp_path, {str(older): "Chromium 141.0.7390.37 \n"})
    host = _host("linux", tmp_path, **{PLAYWRIGHT_BROWSERS_ENV: str(cache)})

    found = await ChromiumLocator(host).locate(cell.session)

    assert found.path == str(older)
    assert found.version == "Chromium 141.0.7390.37"
    assert cell.calls[0][1:] == ("--version",)


async def test_a_path_name_is_verified_by_its_version(tmp_path: Path) -> None:
    cell = _Cell(tmp_path, {"google-chrome": "Google Chrome 120.0.6099.109\n"})

    found = await ChromiumLocator(_host("linux", tmp_path)).locate(cell.session)

    assert found.path == "google-chrome"
    assert [argv[0] for argv in cell.calls] == ["chromium", "chromium-browser", "google-chrome"]


async def test_output_without_a_version_is_not_a_browser(tmp_path: Path) -> None:
    # A transitional package that only says what to install instead exits 0 all the same.
    cell = _Cell(
        tmp_path,
        {
            "chromium": "Command requires the chromium snap\n",
            "microsoft-edge": "Microsoft Edge 1.2",
        },
    )

    found = await ChromiumLocator(_host("linux", tmp_path)).locate(cell.session)

    assert found.path == "microsoft-edge"


async def test_a_candidate_that_hangs_is_skipped(tmp_path: Path) -> None:
    cell = _Cell(tmp_path, {"chromium-browser": "Chromium 140.1"}, slow=frozenset({"chromium"}))

    found = await ChromiumLocator(_host("linux", tmp_path)).locate(cell.session)

    assert found.path == "chromium-browser"


async def test_no_browser_at_all_is_an_attach_error(tmp_path: Path) -> None:
    cell = _Cell(tmp_path, {})

    with pytest.raises(AttachError, match=f"{len(PATH_NAMES)} candidates checked"):
        await ChromiumLocator(_host("linux", tmp_path)).locate(cell.session)


async def test_a_closed_session_is_an_attach_error(tmp_path: Path) -> None:
    cell = _Cell(tmp_path, {"chromium": "Chromium 141.0"})
    await cell.session.close()

    with pytest.raises(AttachError, match="session closed"):
        await ChromiumLocator(_host("linux", tmp_path)).locate(cell.session)


async def test_windows_checks_candidates_with_where_and_never_runs_a_browser(
    tmp_path: Path,
) -> None:
    # Arrange: only Chrome under Program Files exists; `where` prints its full path.
    chrome = PureWindowsPath(r"C:\PF\Google\Chrome\Application\chrome.exe")
    where_spec = f"{chrome.parent}:{chrome.name}"
    cell = _Cell(tmp_path, {where_spec: f"{chrome}\r\n"})
    host = _host(WINDOWS, tmp_path, ProgramFiles=r"C:\PF", LOCALAPPDATA=r"C:\U\L")

    found = await ChromiumLocator(host).locate(cell.session)

    assert found.path == str(chrome)
    assert found.version is None
    assert all(argv[0] == "where" for argv in cell.calls)
    assert ("where", "chromium") in cell.calls  # A bare name is searched for on the PATH.
