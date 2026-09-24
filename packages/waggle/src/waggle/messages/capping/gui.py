"""Define the typed GUI steps an Exoskeleton proposal carries: GuiStep, GuiOp, ElementTarget.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). The
Exoskeleton is the optional set of peripherals a Cell (a unit of compute a Worker runs in or on)
can be given for one task: a display to see, a keyboard and mouse to drive, audio, and a browser.
Every action a Worker takes through it is a Capping proposal (the quality gate every side effect
passes), and ``ActionKind.GUI`` (protocol 1.6, roadmap step 6.5, ADR-0032) is its shape: one or
more ``GuiStep``s the gate applies through the attached Exoskeleton, never prose it would have to
interpret. A step names one ``GuiOp`` and carries exactly the fields that op needs: coordinates
and a button for the desktop pointer, text or a key chord for the keyboard, a URL or an
``ElementTarget`` (an accessible role and name, a label, visible text or a CSS selector, in that
order of preference) for the browser, or a scratch path for a clip to speak. Typed text carries a
``secret`` flag so the flight recorder and every rendering of the step redact it; the gate still
needs the text itself to type it. Every bound is a named constant here; the number, not the name,
is normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Imported by waggle.messages.capping.action, whose
    ProposedAction carries a tuple of GuiStep for a GUI action; built by the Exoskeleton tools
    (hivemind.workers.tools) and applied by the Capping gate's GUI surface (hivemind.exoskeleton).
    Calls into waggle.messages.base only.

Key invariants:
    - A step populates exactly the fields its op needs and none that belong to another op
      (``GuiStep._fields_match_op``); an unused field is None, never an empty string.
    - ``ElementTarget`` names an element exactly one way: by role (optionally with a name), by
      label, by visible text, or by CSS selector.
    - ``secret`` is only ever set on a step that types text (TYPE, BROWSER_FILL).
    - A URL is http, https, file or about:blank; nothing else can be navigated to.

See Also:
    - docs/waggle/spec.md section 8.9 for the normative fields, bounds and validators.
    - docs/adr/0032-gui-actions-are-capped-recorded-and-rolled-back-by-checkpoint.md for why GUI
      actions are typed steps rather than prose.
    - waggle.messages.capping.action for ProposedAction, which carries these steps.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field, field_validator, model_validator

from waggle.messages.base import MAX_PATH_CHARS, VALUE_MODEL_CONFIG

MAX_COORDINATE = 16_384  # Wider than any display a Cell starts; a larger value is a typo.
MAX_SCROLL_STEPS = 100  # Wheel steps in one scroll; more is a different page, not a scroll.
MAX_GUI_TEXT_CHARS = 4_000  # One field's worth of typing; a document goes through a file.
MAX_KEYS_CHARS = 64  # One chord ("ctrl+shift+t") or one named key ("Return").
MAX_URL_CHARS = 2_048  # The de facto browser limit on a URL a person could still read.
MAX_TARGET_CHARS = 512  # A role, an accessible name, a label, visible text or a CSS selector.
MIN_TEXT_CHARS = 1  # Typing nothing is not a step.
# A key chord: one or more key names joined by "+", each an xdotool / Playwright-style name
# ("ctrl", "shift", "Return", "a", "F5"). Letters, digits and underscore only, so no shell or
# injection metacharacter can ever reach the tool that presses it.
KEYS_PATTERN = r"^[A-Za-z0-9_]+(\+[A-Za-z0-9_]+)*$"
# The URL schemes a GUI step may navigate to: the web, a local file (a fixture site in scratch)
# and the blank page. javascript:, data: and every other scheme are refused outright.
_URL_SCHEMES = re.compile(r"^(https?://|file://|about:blank$)", re.IGNORECASE)
# The prefixes an ELEMENT_TEXT subject may name its element by; anything else is a CSS selector.
_SUBJECT_KEYS = frozenset({"role", "label", "text", "selector"})

__all__ = [
    "KEYS_PATTERN",
    "MAX_COORDINATE",
    "MAX_GUI_TEXT_CHARS",
    "MAX_KEYS_CHARS",
    "MAX_SCROLL_STEPS",
    "MAX_TARGET_CHARS",
    "MAX_URL_CHARS",
    "MIN_TEXT_CHARS",
    "ElementTarget",
    "GuiOp",
    "GuiStep",
    "MouseButton",
]


class GuiOp(Enum):
    """What one GUI step does; the op decides which GuiStep fields must be set."""

    MOVE = "MOVE"  # Desktop: move the pointer to (x, y).
    CLICK = "CLICK"  # Desktop: click `button` (LEFT when None) at (x, y).
    DOUBLE_CLICK = "DOUBLE_CLICK"  # Desktop: double-click `button` at (x, y).
    TYPE = "TYPE"  # Desktop: type `text` into whatever has keyboard focus.
    PRESS = "PRESS"  # Desktop: press the chord `keys` ("ctrl+s", "Return").
    SCROLL = "SCROLL"  # Desktop: scroll `dx`/`dy` wheel steps, at (x, y) when given.
    NAVIGATE = "NAVIGATE"  # Browser: load `url` in the attached browser's page.
    BROWSER_CLICK = "BROWSER_CLICK"  # Browser: click the element `target` names.
    BROWSER_FILL = "BROWSER_FILL"  # Browser: replace the value of `target` with `text`.
    BROWSER_PRESS = "BROWSER_PRESS"  # Browser: press `keys`, on `target` when given.
    SAY = "SAY"  # Audio: play the WAV at scratch path `clip` into the Cell's microphone.


class MouseButton(Enum):
    """Which pointer button a click presses."""

    LEFT = "LEFT"
    MIDDLE = "MIDDLE"
    RIGHT = "RIGHT"


# The fields each op requires (all must be set) and allows (may be set); everything else must be
# None. One table, read by GuiStep._fields_match_op, so adding an op is one row here.
_REQUIRED: dict[GuiOp, frozenset[str]] = {
    GuiOp.MOVE: frozenset({"x", "y"}),
    GuiOp.CLICK: frozenset({"x", "y"}),
    GuiOp.DOUBLE_CLICK: frozenset({"x", "y"}),
    GuiOp.TYPE: frozenset({"text"}),
    GuiOp.PRESS: frozenset({"keys"}),
    GuiOp.SCROLL: frozenset(),
    GuiOp.NAVIGATE: frozenset({"url"}),
    GuiOp.BROWSER_CLICK: frozenset({"target"}),
    GuiOp.BROWSER_FILL: frozenset({"target", "text"}),
    GuiOp.BROWSER_PRESS: frozenset({"keys"}),
    GuiOp.SAY: frozenset({"clip"}),
}
_ALLOWED: dict[GuiOp, frozenset[str]] = {
    GuiOp.CLICK: frozenset({"button"}),
    GuiOp.DOUBLE_CLICK: frozenset({"button"}),
    GuiOp.SCROLL: frozenset({"x", "y", "dx", "dy"}),
    GuiOp.BROWSER_PRESS: frozenset({"target"}),
}
_OPTIONAL_FIELDS = ("x", "y", "button", "text", "keys", "dx", "dy", "url", "target", "clip")
_SECRET_OPS = frozenset({GuiOp.TYPE, GuiOp.BROWSER_FILL})  # The only ops that type text.

_Coordinate = Annotated[int, Field(ge=0, le=MAX_COORDINATE)]
_Scroll = Annotated[int, Field(ge=-MAX_SCROLL_STEPS, le=MAX_SCROLL_STEPS)]
_TargetText = Annotated[str, Field(min_length=1, max_length=MAX_TARGET_CHARS)]


class ElementTarget(BaseModel):
    """Name one element on a page: by accessible role and name, label, visible text or selector.

    The browser fast path resolves it; role and name come first because they are what an
    accessibility tree shows a model that cannot see the screen.
    """

    model_config = VALUE_MODEL_CONFIG

    role: _TargetText | None = Field(
        default=None, description="An ARIA role ('button', 'link', 'textbox'); pairs with name."
    )
    name: _TargetText | None = Field(
        default=None, description="The accessible name of the element with `role`; role only."
    )
    label: _TargetText | None = Field(
        default=None, description="The text of the label associated with a form control."
    )
    text: _TargetText | None = Field(default=None, description="Visible text the element contains.")
    selector: _TargetText | None = Field(
        default=None, description="A CSS selector; the last resort when nothing else names it."
    )

    @model_validator(mode="after")
    def _exactly_one_way(self) -> ElementTarget:
        """Require exactly one of role, label, text, selector; name only beside role."""
        ways = [value for value in (self.role, self.label, self.text, self.selector) if value]
        if len(ways) != 1:
            raise ValueError(
                "An ElementTarget names its element exactly one way (role, label, text or "
                f"selector), got {len(ways)}."
            )
        if self.name is not None and self.role is None:
            raise ValueError("ElementTarget.name is the accessible name of `role`; set role too.")
        return self

    @classmethod
    def from_subject(cls, subject: str) -> ElementTarget:
        """Parse the element an ELEMENT_TEXT postcondition's `subject` names (spec section 8.3).

        Args:
            subject: ``role=<role>`` optionally followed by ``;name=<accessible name>``,
                ``label=<label>``, ``text=<visible text>``, ``selector=<css>``, or a bare CSS
                selector (the 1.0 reading, still accepted).

        Returns:
            The target; anything without a known ``key=`` prefix is a CSS selector.

        Raises:
            ValueError: A known form with nothing after the ``=``.
        """
        key, separator, rest = subject.partition("=")
        if not separator or key not in _SUBJECT_KEYS:
            return cls(selector=subject)  # "input[name=q]" has an "=" but no known key.
        if key == "role":
            role, _, name = rest.partition(";name=")
            return cls(role=role, name=name or None)
        return cls.model_validate({key: rest})

    def subject(self) -> str:
        """Render this target as an ELEMENT_TEXT subject, the form `from_subject` reads back.

        Returns:
            For example ``role=button;name=Log in`` or ``selector=#submit``.
        """
        if self.role is not None:
            return f"role={self.role}" + (f";name={self.name}" if self.name else "")
        if self.label is not None:
            return f"label={self.label}"
        if self.text is not None:
            return f"text={self.text}"
        return f"selector={self.selector}"

    def describe(self) -> str:
        """Render this target as one short line, for a recording or a judge's prompt.

        Returns:
            For example ``role=button name='Log in'`` or ``selector='#submit'``.
        """
        if self.role is not None:
            return f"role={self.role}" + (f" name={self.name!r}" if self.name else "")
        if self.label is not None:
            return f"label={self.label!r}"
        if self.text is not None:
            return f"text={self.text!r}"
        return f"selector={self.selector!r}"


class GuiStep(BaseModel):
    """One typed GUI step: an op and exactly the fields that op needs.

    Carried in order by a ``ProposedAction`` of kind GUI; the Capping gate applies the steps
    through the attached Exoskeleton, one after another, and stops at the first that fails.
    """

    model_config = VALUE_MODEL_CONFIG

    op: GuiOp = Field(description="What this step does.")
    x: _Coordinate | None = Field(default=None, description="Pointer x in display pixels.")
    y: _Coordinate | None = Field(default=None, description="Pointer y in display pixels.")
    button: MouseButton | None = Field(
        default=None, description="The button a click presses; None means LEFT."
    )
    text: Annotated[str, Field(min_length=MIN_TEXT_CHARS, max_length=MAX_GUI_TEXT_CHARS)] | None = (
        Field(default=None, description="The text TYPE types or BROWSER_FILL enters.")
    )
    secret: bool = Field(
        default=False,
        description="Whether `text` is secret (a password, a token): redacted everywhere it is "
        "recorded or rendered. Only on TYPE and BROWSER_FILL.",
    )
    keys: Annotated[str, Field(max_length=MAX_KEYS_CHARS, pattern=KEYS_PATTERN)] | None = Field(
        default=None, description="One key chord for PRESS/BROWSER_PRESS: 'ctrl+s', 'Return'."
    )
    dx: _Scroll | None = Field(default=None, description="Horizontal wheel steps; + is right.")
    dy: _Scroll | None = Field(default=None, description="Vertical wheel steps; + is down.")
    url: Annotated[str, Field(max_length=MAX_URL_CHARS)] | None = Field(
        default=None, description="Where NAVIGATE goes: http(s), file or about:blank."
    )
    target: ElementTarget | None = Field(
        default=None, description="The element a browser step acts on."
    )
    clip: Annotated[str, Field(min_length=1, max_length=MAX_PATH_CHARS)] | None = Field(
        default=None, description="The scratch path of the WAV clip SAY plays."
    )

    @field_validator("url")
    @classmethod
    def _url_scheme_is_navigable(cls, value: str | None) -> str | None:
        """Refuse any URL scheme but http, https, file and about:blank."""
        # javascript: and data: URLs would run model-written code in the page; nothing a GUI
        # step legitimately needs lives behind any other scheme.
        if value is not None and not _URL_SCHEMES.match(value):
            raise ValueError(
                f"A GUI step may navigate to http(s), file or about:blank, not {value!r}."
            )
        return value

    @model_validator(mode="after")
    def _fields_match_op(self) -> GuiStep:
        """Require the op's fields, allow its optional ones, and refuse every other field."""
        required = _REQUIRED[self.op]
        allowed = required | _ALLOWED.get(self.op, frozenset())
        present = {name for name in _OPTIONAL_FIELDS if getattr(self, name) is not None}
        missing = sorted(required - present)
        extra = sorted(present - allowed)
        if missing or extra:
            raise ValueError(
                f"A {self.op.value} step requires {sorted(required) or 'nothing more'}; "
                f"missing {missing}, not allowed {extra}."
            )
        # A scroll that moves nowhere is not a step; one axis is enough.
        if self.op is GuiOp.SCROLL and not (self.dx or self.dy):
            raise ValueError("A SCROLL step needs a non-zero dx or dy.")
        if self.secret and self.op not in _SECRET_OPS:
            raise ValueError("Only TYPE and BROWSER_FILL type text, so only they are secret.")
        return self

    def text_chars(self) -> int:
        """Return the characters this step carries, for ProposedAction's total bound.

        Returns:
            The summed length of text, keys, url, clip and every target string.
        """
        parts = (self.text, self.keys, self.url, self.clip)
        total = sum(len(part) for part in parts if part is not None)
        if self.target is not None:
            total += len(self.target.describe())
        return total

    def describe(self) -> str:
        """Render this step as one line with any secret redacted, for a recording or a judge.

        Returns:
            For example ``CLICK LEFT at (120, 340)`` or ``BROWSER_FILL label='Password' with
            [redacted: 9 chars]``.
        """
        text = None
        if self.text is not None:
            text = f"[redacted: {len(self.text)} chars]" if self.secret else repr(self.text)
        # Both clicks read the same way: the button (LEFT when unset) and the point.
        click = f"{(self.button or MouseButton.LEFT).value} at ({self.x}, {self.y})"
        detail = {
            GuiOp.MOVE: f"to ({self.x}, {self.y})",
            GuiOp.CLICK: click,
            GuiOp.DOUBLE_CLICK: click,
            GuiOp.TYPE: f"{text}",
            GuiOp.PRESS: f"{self.keys}",
            GuiOp.SCROLL: f"dx={self.dx or 0} dy={self.dy or 0}",
            GuiOp.NAVIGATE: f"{self.url}",
            GuiOp.BROWSER_CLICK: _target(self.target),
            GuiOp.BROWSER_FILL: f"{_target(self.target)} with {text}",
            GuiOp.BROWSER_PRESS: f"{self.keys} on {_target(self.target)}",
            GuiOp.SAY: f"{self.clip}",
        }[self.op]
        return f"{self.op.value} {detail}"


def _target(target: ElementTarget | None) -> str:
    """Render an optional ElementTarget, or 'the page' when there is none."""
    return target.describe() if target is not None else "the page"
