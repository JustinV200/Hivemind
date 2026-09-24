"""Define BrowserState: the cookies and local storage a browser checkpoint carries, serialised.

A browser checkpoint (`BrowserCheckpoint`, the Capping gate's undo point for a GUI action,
ADR-0032) is a URL plus an opaque `state` string that only the browser which took it reads back.
Both browsers, the Playwright one and the fake, put the same thing in that string: every cookie in
the browser and the local storage of every origin it has visited, as a `BrowserState` serialised to
JSON. One model for both keeps the fake honest (its checkpoint round-trips through exactly the
shape the real one does) and gives the string a validated reader: pydantic parses it back, and
anything else, a checkpoint from another kind of browser or a corrupted one, is refused rather
than half-applied. `storage_origin` names the local-storage area a page reads: its scheme, host and
port for http and https, one shared area for every file:// page (Chromium keeps a single one for
them all), and none for about:blank.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.exoskeleton.browser`.
    Written and read by `browser.playwright` and `browser.fake` in `checkpoint` and `restore`.
    Calls into pydantic and the standard library only.

Key invariants:
    - `BrowserState.load(state.dump()) == state` for every state.
    - A cookie's value and a stored item's value never appear in a repr: they may be session
      tokens (codingrules section 12).

See Also:
    - docs/adr/0032-gui-actions-are-capped-recorded-and-rolled-back-by-checkpoint.md for why a
      checkpoint holds cookies and local storage.
    - hivemind.exoskeleton.browser.base for BrowserCheckpoint.
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

FILE_ORIGIN = "file://"  # The one local-storage area every file:// page shares in Chromium.
_WEB_SCHEMES = {"http": 80, "https": 443}  # The schemes whose origin is scheme, host and port.

__all__ = ["FILE_ORIGIN", "BrowserState", "StoredCookie", "storage_origin"]


class StoredCookie(BaseModel):
    """One cookie as the browser reported it, kept whole so a restore puts back exactly it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(description="The cookie's name.")
    value: str = Field(repr=False, description="Its value; may be a session token, never logged.")
    domain: str = Field(description="The domain it is sent to, as the browser reported it.")
    path: str = Field(description="The path prefix it is sent for.")
    expires: float = Field(description="Unix time it expires at; -1 for a session cookie.")
    http_only: bool = Field(description="Whether page scripts are kept from reading it.")
    secure: bool = Field(description="Whether it is sent over https only.")
    same_site: Literal["Lax", "None", "Strict"] = Field(description="Its SameSite policy.")
    partition_key: str | None = Field(
        default=None, description="The top-level site of a partitioned cookie; None otherwise."
    )


class BrowserState(BaseModel):
    """Every cookie and every visited origin's local storage, as one checkpoint holds them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cookies: tuple[StoredCookie, ...] = Field(
        default=(), description="Every cookie the browser held, in the order it reported them."
    )
    local_storage: dict[str, dict[str, str]] = Field(
        default_factory=dict,
        repr=False,
        description="Origin (storage_origin's form) to that origin's items, key to value; an "
        "origin with nothing stored may be absent.",
    )

    def dump(self) -> str:
        """Serialise to the opaque string a BrowserCheckpoint carries.

        Returns:
            This state as JSON.
        """
        return self.model_dump_json()

    @classmethod
    def load(cls, state: str) -> BrowserState:
        """Parse a checkpoint's state string back.

        Args:
            state: What `dump` produced.

        Returns:
            The state.

        Raises:
            ValueError: `state` is not a BrowserState (pydantic's ValidationError is one).
        """
        return cls.model_validate_json(state)


def storage_origin(url: str) -> str | None:
    """Return the local-storage area a page at `url` reads and writes, or None when it has none.

    Args:
        url: A page URL, as the browser reports it.

    Returns:
        "scheme://host[:port]" for http and https (the port only when it is not the scheme's
        default, as `location.origin` writes it), FILE_ORIGIN for any file:// URL, and None for
        everything else (about:blank, data:, an error page).

    Example:
        >>> storage_origin("https://fixture.test:443/login?next=/")
        'https://fixture.test'
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme == "file":
        return FILE_ORIGIN
    if scheme not in _WEB_SCHEMES or not parts.hostname:
        return None
    try:
        port = parts.port
    except ValueError:
        return None  # A port that is not a number: no page could have loaded from it.
    # An IPv6 literal keeps its brackets in an origin; urlsplit strips them from hostname.
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    suffix = f":{port}" if port is not None and port != _WEB_SCHEMES[scheme] else ""
    return f"{scheme}://{host}{suffix}"
