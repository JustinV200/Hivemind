"""Unit tests for hivemind.exoskeleton.browser.playwright.hosts.answers: a fetch's answer."""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.async_api")

from hivemind.exoskeleton.browser.playwright.hosts.answers import (
    POLICY_HEADER,
    reentry_page,
    with_policy,
)


def test_with_policy_adds_the_policy_to_a_response_that_has_none() -> None:
    headers = {"content-type": "text/html"}

    merged = with_policy(headers, "default-src x")

    assert merged == {"content-type": "text/html", POLICY_HEADER: "default-src x"}
    assert headers == {"content-type": "text/html"}  # The fetched headers are left as they were.


def test_with_policy_keeps_the_sites_own_policy_enforced_beside_the_leases() -> None:
    merged = with_policy({POLICY_HEADER: "script-src 'self'"}, "default-src x")

    assert merged[POLICY_HEADER] == "script-src 'self', default-src x"


def test_with_no_policy_the_headers_are_unchanged() -> None:
    assert with_policy({POLICY_HEADER: "a"}, None) == {POLICY_HEADER: "a"}


def test_the_reentry_page_replaces_itself_with_the_target() -> None:
    page = reentry_page("https://a.example/next?x=1&y=2")

    assert 'location.replace("https://a.example/next?x=1&y=2")' in page
    assert 'content="0;url=https://a.example/next?x=1&amp;y=2"' in page
    assert 'name="referrer" content="no-referrer"' in page


def test_a_target_cannot_close_the_script_or_the_attribute_it_is_written_into() -> None:
    page = reentry_page('https://a.example/"></script><script>alert(1)</script>')

    assert page.count("</script>") == 1  # Only the page's own closing tag.
    # In the refresh attribute the quote and brackets are escaped, so it is not broken out of.
    assert "url=https://a.example/&quot;&gt;&lt;/script&gt;" in page
