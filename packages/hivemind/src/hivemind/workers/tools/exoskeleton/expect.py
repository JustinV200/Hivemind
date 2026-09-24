"""Turn an action tool's optional `expect` into the postconditions its GUI proposal declares.

Codingrules 8.12: every side effect is a proposal that states, before acting, what should hold
afterwards, and the bee that proposed it never checks that itself. An Exoskeleton action tool (a
click, a keystroke, a page fill; roadmap step 6.5) lets the model state it as `expect`, exactly one
of three shapes (ADR-0032), each a waggle `Postcondition` the Capping gate verifies through the
attached Exoskeleton once the steps are applied: `url` becomes URL_MATCHES on the page (a trailing
`*` matches a prefix), `element` with `text` becomes ELEMENT_TEXT (the element's text contains
`text`; the subject is the element in `ElementTarget.subject()` form), and `region` becomes
REGION_CHANGED (the pixels of "x,y,width,height" differ from before). An expectation the attached
peripherals could never check (a URL with no browser, a region with no display, a region off the
screen) is refused here, before anything is proposed, rather than rolled back and alarmed on.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.tools.exoskeleton`. Called by the
    package's `act` for every action tool. Calls into `hivemind.exoskeleton` (Peripherals), this
    package's `arguments` and `errors`, and waggle's `Postcondition` only.

Key invariants:
    - An action tool's proposal declares at most one postcondition, and only one the attached
      peripherals can check.
    - No expectation is ever checked here: the gate checks it, after applying (codingrules 8.12).

See Also:
    - docs/adr/0032-gui-actions-are-capped-recorded-and-rolled-back-by-checkpoint.md for the three
      postcondition kinds.
    - waggle.messages.labels for Postcondition and each kind's subject and expected rules.
    - hivemind.exoskeleton.surface for how the gate observes each kind.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import JsonValue, ValidationError

from hivemind.exoskeleton import Peripherals
from hivemind.workers.tools.exoskeleton.arguments import field_errors, region_from, target_from
from hivemind.workers.tools.exoskeleton.errors import GuiArgumentError
from waggle.messages.labels import Postcondition, PostconditionKind

PAGE_SUBJECT = "page"  # URL_MATCHES asserts about the page itself; the subject names nothing more.
_SHAPES = ("url", "element", "region")  # The three keys that each name one kind of expectation.
_KEYS = frozenset({*_SHAPES, "text"})  # Everything `expect` may hold; `text` rides with element.

__all__ = ["PAGE_SUBJECT", "expectations"]


def expectations(value: JsonValue | None, peripherals: Peripherals) -> tuple[Postcondition, ...]:
    """Turn a call's `expect` argument into the postconditions its proposal declares.

    Args:
        value: The call's `expect`, as the model wrote it; None when it stated none.
        peripherals: What this task's Exoskeleton has attached, which decides what can be checked.

    Returns:
        No postconditions for no expectation, else exactly one.

    Raises:
        GuiArgumentError: `expect` is not one of the three shapes, or names something the
            attached peripherals could never check.
    """
    if value is None:
        return ()
    if not isinstance(value, dict):
        raise GuiArgumentError("expect must be an object: url, element with text, or region.")
    unknown = sorted(set(value) - _KEYS)
    if unknown:
        raise GuiArgumentError(f"expect takes url, element with text, or region, not {unknown}.")
    named = [key for key in _SHAPES if key in value]
    if len(named) != 1:
        raise GuiArgumentError("expect names exactly one of url, element (with text) or region.")
    return (_BUILDERS[named[0]](value, peripherals),)


def _url(expect: dict[str, JsonValue], peripherals: Peripherals) -> Postcondition:
    """URL_MATCHES on the page: needs the browser, and a URL (a trailing * matches a prefix)."""
    _text_only_beside_element(expect)
    if peripherals.browser is None:
        raise GuiArgumentError("a url expectation needs the browser, and none is attached.")
    url = expect.get("url")
    if not isinstance(url, str) or not url:
        raise GuiArgumentError("expect.url must be a non-empty string.")
    return _postcondition(PostconditionKind.URL_MATCHES, PAGE_SUBJECT, url)


def _element(expect: dict[str, JsonValue], peripherals: Peripherals) -> Postcondition:
    """ELEMENT_TEXT: needs the browser, one element, and the text it should contain."""
    if peripherals.browser is None:
        raise GuiArgumentError("an element expectation needs the browser, and none is attached.")
    target = target_from(expect.get("element"), "expect.element")
    text = expect.get("text")
    if not isinstance(text, str) or not text:
        raise GuiArgumentError("expect.element needs expect.text: what the element should say.")
    return _postcondition(PostconditionKind.ELEMENT_TEXT, target.subject(), text)


def _region(expect: dict[str, JsonValue], peripherals: Peripherals) -> Postcondition:
    """REGION_CHANGED: needs a display, and a rectangle that lies on its screen."""
    _text_only_beside_element(expect)
    eye = peripherals.compound_eye
    if eye is None:
        raise GuiArgumentError("a region expectation needs a display, and none is attached.")
    region = region_from(expect.get("region"), eye.screen, "expect.region")
    return _postcondition(PostconditionKind.REGION_CHANGED, region.spec(), None)


def _text_only_beside_element(expect: dict[str, JsonValue]) -> None:
    """Refuse a `text` beside url or region: it only ever says what an element should contain."""
    if "text" in expect:
        raise GuiArgumentError("expect.text goes with expect.element only.")


def _postcondition(kind: PostconditionKind, subject: str, expected: str | None) -> Postcondition:
    """Build one validated postcondition, turning a broken bound into a readable refusal."""
    try:
        return Postcondition(kind=kind, subject=subject, argv=(), expected=expected)
    except ValidationError as error:
        raise GuiArgumentError(f"expect: {field_errors(error)}") from error


# One builder per shape, looked up by the key the model named rather than matched in a chain.
_BUILDERS: dict[str, Callable[[dict[str, JsonValue], Peripherals], Postcondition]] = {
    "url": _url,
    "element": _element,
    "region": _region,
}
