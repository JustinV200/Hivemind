"""Unit tests for hivemind.exoskeleton.recorder.redact: what the recorder scrubs."""

from __future__ import annotations

import pytest

from hivemind.exoskeleton.recorder.redact import MASK, scrub_text, scrub_url


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        (
            "key sk-ant-api03-abcdefghijklmnopqrstuv in the page",
            "sk-ant-api03-abcdefghijklmnopqrstuv",
        ),
        ("Authorization: Bearer abc.def-ghi_jklmnop123", "abc.def-ghi_jklmnop123"),
        ("password=hunter2 remember=1", "hunter2"),
        ("token: 'tok_12345'", "tok_12345"),
        ("jwt eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2QT4", "eyJhbGciOiJIUzI1"),
    ],
)
def test_credential_shapes_never_survive_scrubbing(text: str, secret: str) -> None:
    scrubbed = scrub_text(text, 1_000)

    assert secret not in scrubbed
    assert MASK in scrubbed


def test_a_key_value_pair_keeps_its_key_so_the_reader_knows_what_was_there() -> None:
    assert scrub_text("password=hunter2", 100) == f"password={MASK}"


def test_scrub_text_bounds_the_length() -> None:
    assert len(scrub_text("a" * 1_000, 50)) == 50


def test_scrub_url_masks_secret_parameters_and_credentials_but_keeps_the_place() -> None:
    url = "https://user:pw@example.org/cb?code=xyz&state=ok&access_token=abc"

    scrubbed = scrub_url(url)

    assert scrubbed.startswith("https://example.org/cb?")
    assert "xyz" not in scrubbed and "abc" not in scrubbed and "pw" not in scrubbed
    assert "state=ok" in scrubbed


def test_a_url_without_a_query_is_unchanged() -> None:
    assert scrub_url("file:///s/site/welcome.html") == "file:///s/site/welcome.html"
