"""Pin the typed GUI step of protocol 1.6: GuiStep, GuiOp, ElementTarget and ProposedAction.gui.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises waggle.messages.capping.gui and the
    GUI half of waggle.messages.capping.action against spec section 8.9: every op's required and
    forbidden fields, the element target's one-way rule, the URL schemes, the secret flag and its
    redaction, and the GUI action kind's own field rule.

Key invariants:
    - None: this module holds tests only.

See Also:
    - docs/waggle/spec.md section 8.9 for GuiStep, GuiOp and ElementTarget.
    - docs/adr/0032-gui-actions-are-capped-recorded-and-rolled-back-by-checkpoint.md.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from waggle.messages.capping import (
    ActionKind,
    ElementTarget,
    GuiOp,
    GuiStep,
    MouseButton,
    ProposedAction,
)
from waggle.messages.capping.action import MAX_GUI_STEPS
from waggle.messages.capping.gui import MAX_COORDINATE, MAX_SCROLL_STEPS

# One valid step per op: the minimum each op needs, so a test can add one field and see it refused.
_VALID: dict[GuiOp, dict[str, object]] = {
    GuiOp.MOVE: {"x": 1, "y": 2},
    GuiOp.CLICK: {"x": 1, "y": 2},
    GuiOp.DOUBLE_CLICK: {"x": 1, "y": 2},
    GuiOp.TYPE: {"text": "hello"},
    GuiOp.PRESS: {"keys": "ctrl+s"},
    GuiOp.SCROLL: {"dy": 3},
    GuiOp.NAVIGATE: {"url": "http://127.0.0.1:8080/login"},
    GuiOp.BROWSER_CLICK: {"target": {"role": "button", "name": "Log in"}},
    GuiOp.BROWSER_FILL: {"target": {"label": "Username"}, "text": "alice"},
    GuiOp.BROWSER_PRESS: {"keys": "Enter"},
    GuiOp.SAY: {"clip": "clips/hello.wav"},
}


def _step(op: GuiOp, **overrides: object) -> GuiStep:
    """A valid GuiStep for `op`, then `overrides` applied."""
    return GuiStep.model_validate({"op": op, **_VALID[op], **overrides})


def _gui_action(*steps: GuiStep) -> ProposedAction:
    """A GUI ProposedAction carrying `steps`."""
    return ProposedAction(
        kind=ActionKind.GUI,
        summary="drive the page",
        diff=None,
        diff_sha256=None,
        command=(),
        cwd=None,
        paths=(),
        steps=(),
        gui=steps,
    )


def test_gui_op_members_match_the_spec() -> None:
    assert [op.name for op in GuiOp] == [
        "MOVE",
        "CLICK",
        "DOUBLE_CLICK",
        "TYPE",
        "PRESS",
        "SCROLL",
        "NAVIGATE",
        "BROWSER_CLICK",
        "BROWSER_FILL",
        "BROWSER_PRESS",
        "SAY",
    ]
    assert all(op.value == op.name for op in GuiOp)
    assert [button.name for button in MouseButton] == ["LEFT", "MIDDLE", "RIGHT"]


@pytest.mark.parametrize("op", list(GuiOp))
def test_every_op_constructs_with_its_own_fields_and_round_trips(op: GuiOp) -> None:
    step = _step(op)

    assert GuiStep.model_validate_json(step.model_dump_json()) == step


@pytest.mark.parametrize(
    ("op", "missing"),
    [
        (GuiOp.MOVE, "y"),
        (GuiOp.CLICK, "x"),
        (GuiOp.TYPE, "text"),
        (GuiOp.PRESS, "keys"),
        (GuiOp.NAVIGATE, "url"),
        (GuiOp.BROWSER_CLICK, "target"),
        (GuiOp.BROWSER_FILL, "text"),
        (GuiOp.SAY, "clip"),
    ],
)
def test_a_step_missing_a_required_field_is_refused(op: GuiOp, missing: str) -> None:
    fields = {**_VALID[op], missing: None}

    with pytest.raises(ValidationError, match="missing"):
        GuiStep.model_validate({"op": op, **fields})


@pytest.mark.parametrize(
    ("op", "extra"),
    [
        (GuiOp.MOVE, {"text": "x"}),
        (GuiOp.TYPE, {"x": 3}),
        (GuiOp.NAVIGATE, {"target": {"text": "Go"}}),
        (GuiOp.BROWSER_CLICK, {"url": "https://example.test"}),
        (GuiOp.PRESS, {"button": "LEFT"}),
        (GuiOp.SAY, {"keys": "Return"}),
    ],
)
def test_a_step_carrying_another_ops_field_is_refused(op: GuiOp, extra: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="not allowed"):
        _step(op, **extra)


def test_optional_fields_are_accepted_where_the_op_allows_them() -> None:
    assert _step(GuiOp.CLICK, button=MouseButton.RIGHT).button is MouseButton.RIGHT
    assert _step(GuiOp.SCROLL, x=5, y=6, dx=-2).dx == -2
    assert _step(GuiOp.BROWSER_PRESS, target={"text": "Search"}).target is not None


def test_a_scroll_that_moves_nowhere_is_refused() -> None:
    with pytest.raises(ValidationError, match="non-zero"):
        GuiStep(op=GuiOp.SCROLL, dx=0, dy=0)


@pytest.mark.parametrize("value", [-1, MAX_COORDINATE + 1])
def test_coordinates_are_bounded(value: int) -> None:
    with pytest.raises(ValidationError):
        _step(GuiOp.CLICK, x=value)


@pytest.mark.parametrize("value", [-MAX_SCROLL_STEPS - 1, MAX_SCROLL_STEPS + 1])
def test_scroll_steps_are_bounded(value: int) -> None:
    with pytest.raises(ValidationError):
        _step(GuiOp.SCROLL, dy=value)


@pytest.mark.parametrize("keys", ["ctrl+s", "Return", "ctrl+shift+t", "F5", "a"])
def test_key_chords_in_the_documented_syntax_are_accepted(keys: str) -> None:
    assert _step(GuiOp.PRESS, keys=keys).keys == keys


@pytest.mark.parametrize("keys", ["ctrl+", "+s", "ctrl s", "$(reboot)", "a;b", ""])
def test_anything_but_a_key_chord_is_refused(keys: str) -> None:
    # The pattern is what keeps shell and injection metacharacters out of a key name.
    with pytest.raises(ValidationError):
        _step(GuiOp.PRESS, keys=keys)


@pytest.mark.parametrize(
    "url",
    ["https://example.test/a?b=c", "http://127.0.0.1:9/x", "file:///tmp/s/i.html", "about:blank"],
)
def test_navigable_urls_are_accepted(url: str) -> None:
    assert _step(GuiOp.NAVIGATE, url=url).url == url


@pytest.mark.parametrize(
    "url", ["javascript:alert(1)", "data:text/html,<p>", "ftp://example.test", "chrome://settings"]
)
def test_every_other_url_scheme_is_refused(url: str) -> None:
    with pytest.raises(ValidationError, match="navigate"):
        _step(GuiOp.NAVIGATE, url=url)


def test_secret_is_allowed_only_on_steps_that_type() -> None:
    assert _step(GuiOp.TYPE, secret=True).secret
    assert _step(GuiOp.BROWSER_FILL, secret=True).secret
    with pytest.raises(ValidationError, match="secret"):
        _step(GuiOp.CLICK, secret=True)


def test_describe_redacts_secret_text_and_keeps_the_rest() -> None:
    secret = _step(GuiOp.BROWSER_FILL, text="hunter2!", secret=True)
    plain = _step(GuiOp.TYPE, text="hello")

    assert "hunter2" not in secret.describe()
    assert "[redacted: 8 chars]" in secret.describe()
    assert secret.describe().startswith("BROWSER_FILL label='Username'")
    assert plain.describe() == "TYPE 'hello'"


@pytest.mark.parametrize("op", list(GuiOp))
def test_describe_renders_every_op_on_one_line(op: GuiOp) -> None:
    line = _step(op).describe()

    assert line.startswith(op.value)
    assert "\n" not in line


def test_text_chars_counts_every_string_the_step_carries() -> None:
    step = _step(GuiOp.BROWSER_FILL, text="abcd")

    assert step.text_chars() == len("abcd") + len("label='Username'")


def test_element_target_names_its_element_exactly_one_way() -> None:
    assert ElementTarget(role="button", name="Go").describe() == "role=button name='Go'"
    assert ElementTarget(selector="#go").describe() == "selector='#go'"
    assert ElementTarget(text="Go").describe() == "text='Go'"
    with pytest.raises(ValidationError, match="exactly one way"):
        ElementTarget(role="button", label="Go")
    with pytest.raises(ValidationError, match="exactly one way"):
        ElementTarget()


def test_element_target_name_needs_a_role() -> None:
    with pytest.raises(ValidationError, match="set role too"):
        ElementTarget(name="Go", text="Go")


def test_a_gui_action_carries_steps_and_round_trips() -> None:
    action = _gui_action(_step(GuiOp.CLICK), _step(GuiOp.TYPE))

    assert ProposedAction.model_validate_json(action.model_dump_json()) == action


def test_a_gui_action_without_steps_is_refused() -> None:
    with pytest.raises(ValidationError, match="gui is non-empty exactly for a GUI action"):
        _gui_action()


def test_steps_on_another_action_kind_are_refused() -> None:
    with pytest.raises(ValidationError, match="gui is non-empty exactly for a GUI action"):
        ProposedAction(
            kind=ActionKind.COMMAND,
            summary="run",
            diff=None,
            diff_sha256=None,
            command=("true",),
            cwd=None,
            paths=(),
            steps=(),
            gui=(_step(GuiOp.CLICK),),
        )


def test_a_gui_action_is_bounded_in_steps() -> None:
    with pytest.raises(ValidationError):
        _gui_action(*[_step(GuiOp.CLICK)] * (MAX_GUI_STEPS + 1))
