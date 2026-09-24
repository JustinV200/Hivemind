"""Unit tests for hivemind.exoskeleton.recorder.redact: what the recorder scrubs."""

from __future__ import annotations

import pytest

from hivemind.exoskeleton.recorder.redact import (
    MASK,
    describe_step,
    redact_step,
    scrub_text,
    scrub_url,
)
from waggle.messages.capping import ElementTarget, GuiOp, GuiStep


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


def test_redact_step_masks_a_secret_but_keeps_the_step_a_procedure_needs() -> None:
    fill = GuiStep(
        op=GuiOp.BROWSER_FILL, target=ElementTarget(label="Password"), text="hunter2", secret=True
    )

    redacted = redact_step(fill)

    assert (redacted.op, redacted.target, redacted.secret) == (fill.op, fill.target, True)
    assert redacted.text == MASK


def test_redact_step_scrubs_typed_text_and_masks_a_navigation_url() -> None:
    typed = GuiStep(op=GuiOp.TYPE, text="export API_KEY=sk-live-abcdefghijklmnop")
    navigate = GuiStep(op=GuiOp.NAVIGATE, url="https://site.test/reset?token=abc123&step=2")

    assert "abcdefghijklmnop" not in (redact_step(typed).text or "")
    assert redact_step(navigate).url == f"https://site.test/reset?token={MASK}&step=2"


def test_redact_step_leaves_a_step_with_nothing_to_hide_alone() -> None:
    click = GuiStep(op=GuiOp.BROWSER_CLICK, target=ElementTarget(role="button", name="Log in"))

    assert redact_step(click) is click


def test_describe_step_keeps_a_secrets_length_and_scrubs_everything_else() -> None:
    fill = GuiStep(
        op=GuiOp.BROWSER_FILL, target=ElementTarget(label="Password"), text="hunter2", secret=True
    )
    typed = GuiStep(op=GuiOp.TYPE, text="password=hunter2")

    assert describe_step(fill) == "BROWSER_FILL label='Password' with [redacted: 7 chars]"
    assert describe_step(typed) == f"TYPE 'password={MASK}'"
