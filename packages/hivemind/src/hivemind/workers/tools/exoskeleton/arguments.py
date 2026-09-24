"""Define the arguments the Exoskeleton tools share: their JSON schemas, and their parsers.

Every Exoskeleton tool (the Worker's hands, eyes and ears on its Cell's optional display, input,
audio and browser) takes some of the same arguments: a point, a screen region, a key chord, an
element on a page named by role and name, label, visible text or CSS selector (waggle's
`ElementTarget`), and, on every action tool, an optional `expect` (what the Capping gate, the
quality gate every side effect passes, should find once the action is applied) and an optional
`irreversible` flag. The JSON schema a model sees and the parser a tool runs sit side by side here
so the two cannot drift. `hivemind.llm.validate_arguments` checks only the top level of a schema,
so every nested object (a target, an expectation) is checked here, and every GUI step is built
through `build_step`, which turns a validation failure into a message naming fields and rules.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.tools.exoskeleton`. Called by the
    package's desktop, browser, look and audio tools and by its `expect` module. Calls into
    `hivemind.exoskeleton` (Region, ScreenSize), `hivemind.llm` (ToolDefinition, JsonObject), this
    package's `errors` and waggle's GUI step models only.

Key invariants:
    - A step's validation failure never quotes its input: pydantic keeps each failing value on
      the error, and a TYPE or BROWSER_FILL step's value is typed text, possibly a secret, so
      `build_step` reports field and rule only and drops the cause.
    - Every action tool's schema carries the same optional `expect` and `irreversible`
      properties (`action_definition`); no read-only tool's schema carries either.

See Also:
    - waggle.messages.capping.gui for GuiStep and ElementTarget, and their bounds.
    - hivemind.workers.tools.exoskeleton.expect for turning `expect` into postconditions.
    - .claude/codingrules.md section 15 for "LLM output is untrusted input".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import JsonValue, ValidationError

from hivemind.exoskeleton import Region, ScreenSize
from hivemind.llm import JsonObject, ToolDefinition
from hivemind.workers.tools.exoskeleton.errors import GuiArgumentError
from waggle.messages.capping import ElementTarget, GuiStep

# An element, named exactly one way; role and name first, because that is what the page's
# accessibility snapshot shows a model that cannot see the screen.
TARGET_SCHEMA: JsonObject = {
    "type": "object",
    "description": "The element, named one way: role (with its accessible name), label, visible "
    "text, or a CSS selector as a last resort. Take role and name from browser_snapshot.",
    "properties": {
        "role": {"type": "string", "description": "An ARIA role: button, link, textbox, heading."},
        "name": {"type": "string", "description": "The accessible name of the element with role."},
        "label": {"type": "string", "description": "The label text of a form field."},
        "text": {"type": "string", "description": "Visible text the element contains."},
        "selector": {"type": "string", "description": "A CSS selector."},
    },
    "additionalProperties": False,
}
# What the Capping gate checks after applying the action; exactly one of three shapes.
EXPECT_SCHEMA: JsonObject = {
    "type": "object",
    "description": "What must hold once the action is applied, checked by the Capping gate: "
    "exactly one of url (the page URL; a trailing * matches a prefix), element with text (that "
    "element's text contains text) or region 'x,y,width,height' (those screen pixels change). "
    "If it does not hold, the action is rolled back and an Alarm is raised.",
    "properties": {
        "url": {"type": "string"},
        "element": TARGET_SCHEMA,
        "text": {"type": "string"},
        "region": {"type": "string"},
    },
    "additionalProperties": False,
}
# The one flag that raises an action to the irreversible tier, which a judge reviews at once.
IRREVERSIBLE_SCHEMA: JsonObject = {
    "type": "boolean",
    "description": "True when this cannot be undone (a message sent, a form submitted to a "
    "server): the action is then reviewed by a judge before your next step.",
}

__all__ = [
    "EXPECT_SCHEMA",
    "IRREVERSIBLE_SCHEMA",
    "TARGET_SCHEMA",
    "action_definition",
    "build_step",
    "field_errors",
    "flag",
    "optional_int",
    "read_definition",
    "region_from",
    "required_int",
    "required_text",
    "target_from",
]


def action_definition(
    name: str, description: str, properties: JsonObject, required: Sequence[str]
) -> ToolDefinition:
    """Build an action tool's definition: its own properties plus `expect` and `irreversible`.

    Args:
        name: The tool's name, as a model calls it.
        description: What the tool does and when to call it.
        properties: The tool's own argument schemas, by name.
        required: The names among `properties` a call must supply.

    Returns:
        The ToolDefinition, its parameters closed to any other property.
    """
    extended: JsonObject = {
        **properties,
        "expect": EXPECT_SCHEMA,
        "irreversible": IRREVERSIBLE_SCHEMA,
    }
    return read_definition(name, description, extended, required)


def read_definition(
    name: str, description: str, properties: JsonObject, required: Sequence[str] = ()
) -> ToolDefinition:
    """Build a tool's definition with exactly `properties`, closed to any other.

    Args:
        name: The tool's name, as a model calls it.
        description: What the tool does and when to call it.
        properties: The tool's argument schemas, by name.
        required: The names among `properties` a call must supply.

    Returns:
        The ToolDefinition.
    """
    parameters: JsonObject = {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }
    return ToolDefinition(name=name, description=description, parameters=parameters)


def required_int(arguments: JsonObject, name: str) -> int:
    """Return integer argument `name`, which the call must supply.

    Args:
        arguments: The call's arguments.
        name: The argument to read.

    Returns:
        Its value.

    Raises:
        GuiArgumentError: It is missing or not an integer.
    """
    value = optional_int(arguments, name)
    if value is None:
        raise GuiArgumentError(f"{name} is required.")
    return value


def optional_int(arguments: JsonObject, name: str) -> int | None:
    """Return integer argument `name`, or None when the call left it out.

    Args:
        arguments: The call's arguments.
        name: The argument to read.

    Returns:
        Its value, or None.

    Raises:
        GuiArgumentError: It is present but not an integer (a bool is not one, though Python's
            own `bool` subclasses `int`).
    """
    value = arguments.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise GuiArgumentError(f"{name} must be an integer.")
    return value


def required_text(arguments: JsonObject, name: str) -> str:
    """Return non-empty string argument `name`; the error never quotes what was given.

    Args:
        arguments: The call's arguments.
        name: The argument to read; it may be typed text, which is why it is never quoted.

    Returns:
        Its value.

    Raises:
        GuiArgumentError: It is missing, not a string, or empty.
    """
    value = arguments.get(name)
    if not isinstance(value, str) or not value:
        raise GuiArgumentError(f"{name} must be a non-empty string.")
    return value


def flag(arguments: JsonObject, name: str) -> bool:
    """Return boolean argument `name`; absent reads as False.

    Args:
        arguments: The call's arguments.
        name: The flag to read ("secret", "double", "irreversible").

    Returns:
        Its value, or False when the call left it out.

    Raises:
        GuiArgumentError: It is present but not a boolean.
    """
    value = arguments.get(name, False)
    if not isinstance(value, bool):
        raise GuiArgumentError(f"{name} must be true or false.")
    return value


def target_from(value: JsonValue | None, name: str = "target") -> ElementTarget:
    """Validate a target argument into the ElementTarget it names.

    Args:
        value: The call's target object, as the model wrote it.
        name: How the error names the argument ("target", "expect.element").

    Returns:
        The ElementTarget.

    Raises:
        GuiArgumentError: It is not an object, or does not name exactly one element one way.
    """
    if not isinstance(value, dict):
        raise GuiArgumentError(
            f"{name} must be an object naming one element: role (with an optional name), "
            "label, text or selector."
        )
    try:
        return ElementTarget.model_validate(value)
    except ValidationError as error:
        raise GuiArgumentError(f"{name}: {field_errors(error)}") from error


def region_from(value: JsonValue | None, screen: ScreenSize, name: str) -> Region:
    """Parse an "x,y,width,height" argument into a Region that lies on `screen`.

    Args:
        value: The call's region text.
        screen: The attached display's size.
        name: How the error names the argument ("region", "expect.region").

    Returns:
        The Region.

    Raises:
        GuiArgumentError: It is not "x,y,width,height" in pixels, or leaves the screen.
    """
    if not isinstance(value, str):
        raise GuiArgumentError(f"{name} must be a string 'x,y,width,height' in pixels.")
    try:
        region = Region.parse(value)
    except ValueError as error:
        raise GuiArgumentError(f"{name} must be 'x,y,width,height' in pixels.") from error
    if not region.fits(screen):
        raise GuiArgumentError(
            f"{name} {region.spec()} does not fit the {screen.width}x{screen.height} screen."
        )
    return region


def build_step(fields: Mapping[str, object]) -> GuiStep:
    """Build one validated GUI step from `fields`, reporting a failure without its values.

    Args:
        fields: The step's op and the fields that op needs (waggle's GuiStep).

    Returns:
        The GuiStep.

    Raises:
        GuiArgumentError: The step breaks one of GuiStep's rules; the message names the field and
            the rule, never the value.
    """
    try:
        return GuiStep.model_validate(dict(fields))
    except ValidationError as error:
        # WHY: the cause is dropped, not chained: it keeps every failing input, and a TYPE or
        # BROWSER_FILL step's input is typed text, possibly a secret (module docstring).
        raise GuiArgumentError(field_errors(error)) from None


def field_errors(error: ValidationError) -> str:
    """Render a validation failure as field and rule, one per problem, never quoting an input.

    Args:
        error: What pydantic raised.

    Returns:
        For example "text: String should have at most 4000 characters".
    """
    problems = error.errors(include_url=False, include_context=False, include_input=False)
    return "; ".join(
        f"{'.'.join(str(part) for part in problem['loc']) or 'value'}: {problem['msg']}"
        for problem in problems
    )
