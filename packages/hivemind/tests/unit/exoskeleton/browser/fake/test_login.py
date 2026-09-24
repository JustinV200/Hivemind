"""Unit tests for hivemind.exoskeleton.browser.fake.login: the fixture site, fake and real alike."""

from __future__ import annotations

from pathlib import Path

import pytest

from hivemind.exoskeleton.browser.fake import (
    FIXTURE_ORIGIN,
    HELP_LINK,
    HELP_TEXT,
    LOGIN_HEADING,
    LOGIN_PASSWORD,
    LOGIN_TITLE,
    LOGIN_USERNAME,
    LONG_HEADING,
    LONG_TEXT,
    LONG_TITLE,
    SESSION_KEY,
    WELCOME_TITLE,
    WRONG_CREDENTIALS,
    login_site,
)

# The real fixture site: tests/fixtures/sites/login/ (this file is tests/unit/exoskeleton/...).
_SITE = Path(__file__).resolve().parents[4] / "fixtures" / "sites" / "login"


def test_the_site_has_its_three_pages_under_the_origin() -> None:
    site = login_site("https://other.test")

    assert [page.url for page in site.pages] == [
        "https://other.test/login",
        "https://other.test/welcome",
        "https://other.test/long",
    ]
    assert [page.title for page in site.pages] == [LOGIN_TITLE, WELCOME_TITLE, LONG_TITLE]


def test_the_welcome_page_is_guarded_by_the_session_item() -> None:
    welcome = login_site().page(f"{FIXTURE_ORIGIN}/welcome")

    assert welcome is not None
    assert welcome.requires == SESSION_KEY
    assert welcome.otherwise == f"{FIXTURE_ORIGIN}/login"


def test_the_long_text_is_past_every_cap() -> None:
    assert len(LONG_TEXT) == 84_000


@pytest.mark.parametrize(
    ("page", "facts"),
    [
        (
            "login.html",
            (
                LOGIN_TITLE,
                LOGIN_HEADING,
                LOGIN_USERNAME,
                LOGIN_PASSWORD,
                SESSION_KEY,
                WRONG_CREDENTIALS,
                HELP_LINK,
                HELP_TEXT,
                "Username",
                "Password",
            ),
        ),
        ("welcome.html", (WELCOME_TITLE, SESSION_KEY, '"Welcome, "')),
        ("long.html", (LONG_TITLE, LONG_HEADING, '"The hive hums along. ".repeat(4000)')),
    ],
)
def test_the_real_fixture_site_says_what_the_fake_one_does(
    page: str, facts: tuple[str, ...]
) -> None:
    html = (_SITE / page).read_text(encoding="utf-8")

    missing = [fact for fact in facts if fact not in html]

    assert missing == []
