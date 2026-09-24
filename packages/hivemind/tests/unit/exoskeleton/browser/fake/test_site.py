"""Unit tests for hivemind.exoskeleton.browser.fake.site: a web site as frozen data."""

from __future__ import annotations

import pytest

from hivemind.exoskeleton.browser.fake import (
    BLANK_PAGE,
    BLANK_URL,
    FakeElement,
    FakePage,
    FakeSite,
)


def test_a_page_refuses_two_elements_with_one_key() -> None:
    with pytest.raises(ValueError, match="two elements with one key"):
        FakePage(url="https://a.test/", title="A", elements=(FakeElement("x"), FakeElement("x")))


def test_a_page_guard_needs_both_halves() -> None:
    with pytest.raises(ValueError, match="needs both"):
        FakePage(url="https://a.test/", title="A", requires="session")


def test_an_element_is_found_by_its_key_and_a_missing_key_is_a_key_error() -> None:
    page = FakePage(url="https://a.test/", title="A", elements=(FakeElement("ok", text="Hi"),))

    assert page.element("ok").text == "Hi"
    with pytest.raises(KeyError):
        page.element("gone")


def test_a_field_is_an_element_with_a_value() -> None:
    assert FakeElement("f", value="").is_field
    assert not FakeElement("t", text="words").is_field


def test_a_site_serves_its_pages_by_url_ignoring_the_fragment() -> None:
    page = FakePage(url="https://a.test/one", title="One")
    site = FakeSite(pages=(page,))

    assert site.page("https://a.test/one#top") is page
    assert site.page(BLANK_URL) is BLANK_PAGE
    assert site.page("https://a.test/two") is None
