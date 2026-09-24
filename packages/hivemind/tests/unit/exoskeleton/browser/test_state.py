"""Unit tests for hivemind.exoskeleton.browser.state: the checkpoint state and storage origins."""

from __future__ import annotations

import pytest

from hivemind.exoskeleton.browser.state import (
    FILE_ORIGIN,
    BrowserState,
    StoredCookie,
    storage_origin,
)


def _cookie(**overrides: object) -> StoredCookie:
    fields: dict[str, object] = {
        "name": "session",
        "value": "token-123",
        "domain": "fixture.test",
        "path": "/",
        "expires": -1.0,
        "http_only": True,
        "secure": True,
        "same_site": "Lax",
    }
    fields.update(overrides)
    return StoredCookie.model_validate(fields)


def test_a_state_round_trips_through_its_string() -> None:
    state = BrowserState(
        cookies=(_cookie(), _cookie(name="p", partition_key="https://top.test")),
        local_storage={"https://fixture.test": {"session": "alice"}, FILE_ORIGIN: {}},
    )

    assert BrowserState.load(state.dump()) == state


def test_loading_anything_else_is_a_value_error() -> None:
    with pytest.raises(ValueError):
        BrowserState.load("not a browser state")
    with pytest.raises(ValueError):
        BrowserState.load('{"cookies": [], "surprise": 1}')


def test_secrets_never_appear_in_a_repr() -> None:
    state = BrowserState(cookies=(_cookie(),), local_storage={"https://a.test": {"k": "hush"}})

    assert "token-123" not in repr(state)
    assert "hush" not in repr(state)


@pytest.mark.parametrize(
    ("url", "origin"),
    [
        ("https://fixture.test/login?next=/", "https://fixture.test"),
        ("https://fixture.test:443/x", "https://fixture.test"),
        ("http://127.0.0.1:8123/login.html", "http://127.0.0.1:8123"),
        ("HTTP://Fixture.TEST:80/", "http://fixture.test"),
        ("http://[::1]:9000/", "http://[::1]:9000"),
        ("file:///home/x/login.html", FILE_ORIGIN),
        ("file:///C:/site/welcome.html", FILE_ORIGIN),
        ("about:blank", None),
        ("chrome-error://chromewebdata/", None),
        ("http://host:notaport/", None),
    ],
)
def test_storage_origin_names_the_area_a_page_reads(url: str, origin: str | None) -> None:
    assert storage_origin(url) == origin
