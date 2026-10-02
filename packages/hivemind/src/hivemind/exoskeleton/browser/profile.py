"""Seed a lease's Chromium profile with the settings that keep its own file dialogs shut.

A lease's browser has a profile of its own in scratch (`--user-data-dir`), so what Chromium reads
from that profile at start is per-lease, unlike its machine-wide policies (written as root under
/etc on Linux, which a Real Cell could not use without changing the operator's machine). Two of
the settings there close what desktop input could otherwise reach (ADR-0059):

- `select_file_dialogs.allowed = false` in `Local State`, the setting the
  `AllowFileSelectionDialogs` policy sets: Save As, Open File, Print to PDF's save and a page's
  file picker are each refused as if cancelled, so no dialog ever lists or writes the Cell's
  directories (checked on Edge 154 on Windows, 2026-10-02: a page's save picker was refused
  with AbortError at once, and the value read back as user-controlled `false`).
- the download directory in `Default/Preferences`, pointed into scratch with no prompt, so a
  download that does happen lands inside the lease rather than in the operator's own folder.

DevTools has a matching setting (`devtools.availability = 2`), but Chromium then refuses remote
debugging too, and the lease drives its browser over exactly that, so DevTools is closed over CDP
instead (`browser.playwright.chrome`). An existing file (an Overwintered profile) is merged into,
never replaced, so the profile's other state survives.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.exoskeleton.browser`.
    Called by `browser.launch.ChromiumLauncher` before Chromium starts. Calls into `hivemind.cell`
    (CellSession and its errors), `hivemind.exoskeleton.errors` and the standard library.

Key invariants:
    - Every write goes through the Cell's session, inside the lease's scratch.
    - A file that cannot be read as a JSON object is replaced by the settings alone.

See Also:
    - docs/adr/0059-a-lease-browser-stays-inside-its-granted-hosts-and-its-own-chrome.md.
    - hivemind.exoskeleton.browser.launch for the launch this prepares.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from hivemind.cell import CellSession, PathNotAllowedError, SessionClosedError
from hivemind.exoskeleton.errors import AttachError

LOCAL_STATE = "Local State"  # Chromium's browser-wide settings file, at the profile's root.
PREFERENCES = Path("Default") / "Preferences"  # The default profile's own settings file.
DOWNLOADS_DIR = "downloads"  # Inside the browser directory: where any download lands.
FILE_CALL_TIMEOUT_S = 10.0  # One small file read or write through the session.

__all__ = ["DOWNLOADS_DIR", "LOCAL_STATE", "PREFERENCES", "lease_settings", "seed_profile"]


def lease_settings(downloads: Path) -> dict[Path, dict[str, object]]:
    """Return the settings each profile file gets, keyed by its path inside the profile.

    Args:
        downloads: The lease's download directory, inside scratch.

    Returns:
        `Local State`'s and `Default/Preferences`' settings, as nested JSON objects.
    """
    folder = str(downloads)
    return {
        Path(LOCAL_STATE): {"select_file_dialogs": {"allowed": False}},
        PREFERENCES: {
            "download": {"prompt_for_download": False, "default_directory": folder},
            "savefile": {"default_directory": folder},
        },
    }


async def seed_profile(session: CellSession, profile: Path, downloads: Path) -> None:
    """Merge the lease's settings into the profile's files, before Chromium starts.

    Latency: two small reads and two small writes through the session, milliseconds.

    Args:
        session: The Cell's session; every file goes through it.
        profile: The profile directory, inside the lease's scratch.
        downloads: Where downloads land, inside scratch.

    Raises:
        AttachError: The session refused or timed out on a file.
    """
    for relative, settings in lease_settings(downloads).items():
        path = profile / relative
        try:
            async with asyncio.timeout(FILE_CALL_TIMEOUT_S):
                merged = merge(_as_object(await _read(session, path)), settings)
                await session.put_file(path, json.dumps(merged).encode())
        except (SessionClosedError, PathNotAllowedError, TimeoutError) as error:
            raise AttachError(f"could not prepare the browser's profile: {error}") from error


def merge(existing: dict[str, object], settings: dict[str, object]) -> dict[str, object]:
    """Return `existing` with `settings` laid over it, nested objects merged key by key.

    Args:
        existing: What the file held, or an empty object.
        settings: The values the lease needs; each wins over what was there.

    Returns:
        A new object; neither argument is changed.
    """
    merged = dict(existing)
    for key, value in settings.items():
        current = merged.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            merged[key] = merge(current, value)
        else:
            merged[key] = value
    return merged


async def _read(session: CellSession, path: Path) -> bytes | None:
    """Return a profile file's bytes, or None when a fresh profile has none yet."""
    try:
        return await session.get_file(path)
    except FileNotFoundError:
        return None


def _as_object(data: bytes | None) -> dict[str, object]:
    """Parse a settings file into a JSON object; anything else counts as empty."""
    if data is None:
        return {}
    try:
        parsed = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}  # A damaged file: the lease's settings alone are written in its place.
    return parsed if isinstance(parsed, dict) else {}
