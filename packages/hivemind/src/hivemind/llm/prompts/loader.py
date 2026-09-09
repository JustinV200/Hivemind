"""Load a named prompt asset and assemble it with labelled, ordered durable-state sections.

A **prompt asset** is one of the plain-markdown files shipped beside this module
(``queen_system.md``, ``decompose_goal.md``, ``warden_system.md``, ``drone_system.md``,
``attendant_triage.md``): the system text a bee's awake episode (a bounded, stateless turn where a
bee is allowed to think with a model) opens with. :func:`load_prompt` reads one such file through
``importlib.resources`` rather than a filesystem path built from ``__file__``, so it works the same
way from an installed wheel as from a checkout. :func:`render` then appends whatever durable state
the caller supplies -- pins, hot state, retrieved content, user-supplied content, the triggering
event -- each wrapped in a plain-text delimiter naming its kind, in the fixed order codingrules
section 8.9 calls "stable prefix first" (pins, hot state, retrieved, user, event), regardless of
the order the caller's mapping happens to iterate in. The labelling exists so a model reading the
assembled text can always tell durable state and retrieved content apart from an instruction to it
(codingrules section 15: "Nothing is executed from the Honey Store... it is delimited and labelled
as retrieved content" when it reaches a prompt).

Fits into the Hive:
    Layer 1 (foundational services), inside hivemind.llm. Called by hivemind.memory.assemble (a
    parallel roadmap step) once it turns durable state into these labelled sections, and by every
    awake episode (queen/awake, wardens/awake) and the Drone's tool loop, which pass the resulting
    text as an LLMRequest's `system` field. Calls into hivemind.common only, per this package's
    import restriction; it never imports hivemind.forage or hivemind.llm's own sibling modules.

Key invariants:
    - Every PromptName maps to exactly one `<name>.md` file shipped inside this package; a missing
      file raises PromptNotFoundError rather than a bare FileNotFoundError.
    - load_prompt never reads from a filesystem path computed relative to `__file__`; it always
      goes through `importlib.resources`, so it behaves the same from a checkout and from a wheel.
    - render() always orders sections PINS, HOT_STATE, RETRIEVED, USER, EVENT regardless of the
      order `sections` iterates in.

See Also:
    - .claude/codingrules.md section 8.6 for "Prompts are portable".
    - .claude/codingrules.md section 8.9 for "Stable prefix first".
    - .claude/codingrules.md section 15 for labelling retrieved/hot-state content as data.
    - hivemind.llm.prompts.README for the portability rules and how to test this package.
"""

from __future__ import annotations

import functools
from collections.abc import Mapping
from enum import Enum
from importlib.resources import files
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from hivemind.common.errors import NotFoundError

# The package these prompt assets ship inside, resolved through importlib.resources rather than a
# path built from __file__ (see the module docstring's "Key invariants").
_PROMPTS_PACKAGE = "hivemind.llm.prompts"

__all__ = [
    "LabelledSection",
    "PromptName",
    "PromptNotFoundError",
    "SectionLabel",
    "load_prompt",
    "render",
]


class PromptName(Enum):
    """One prompt asset shipped as `<value>.md` beside this module."""

    QUEEN_SYSTEM = "queen_system"  # queen/awake: one supervisory decision (roadmap 3.20).
    DECOMPOSE_GOAL = "decompose_goal"  # queen/planner: a goal becomes a TaskGraphDraft (3.20).
    WARDEN_SYSTEM = "warden_system"  # wardens/awake: one intervention decision (3.19).
    DRONE_SYSTEM = "drone_system"  # workers/roles/drone: one tool call per turn (3.16).
    ATTENDANT_TRIAGE = "attendant_triage"  # queen|wardens/inbox: the model tie-breaker (3.13).


class SectionLabel(Enum):
    """What kind of durable state one rendered section carries."""

    PINS = "pins"  # Facts that never decay (memory.pins).
    HOT_STATE = "hot_state"  # Active tasks, open Alarms, pending questions, recent decisions.
    RETRIEVED = "retrieved"  # Honey/Bee Bread content fetched for this episode.
    USER = "user"  # Text a human typed, verbatim.
    EVENT = "event"  # The trigger this episode is answering.


# codingrules 8.9 "Stable prefix first": sections always render in this order, whatever order the
# caller's mapping iterates in, so provider prompt caching sees the same prefix call after call.
_SECTION_ORDER: tuple[SectionLabel, ...] = (
    SectionLabel.PINS,
    SectionLabel.HOT_STATE,
    SectionLabel.RETRIEVED,
    SectionLabel.USER,
    SectionLabel.EVENT,
)


class PromptNotFoundError(NotFoundError):
    """Raise when a prompt asset named by `PromptName` has no matching shipped file.

    Rooted at `hivemind.common.errors.NotFoundError` rather than a new `hivemind.llm` error tree:
    `llm/errors.py` is owned by a separate dispatch landing in this same phase, so there is no
    `LLMError` to root this under yet. Flagged in this step's report for the orchestrator to
    re-parent under that tree once it exists.
    """

    code: ClassVar[str] = "hivemind.llm.prompt_not_found"

    def __init__(self, name: PromptName) -> None:
        """Build the error for a missing prompt asset.

        Args:
            name: The `PromptName` that was looked up and not found.
        """
        super().__init__(f"No prompt asset shipped for {name.value!r}.")
        self.name = name


class LabelledSection(BaseModel):
    """One block of durable state, delimited and labelled by kind before it reaches a model.

    Codingrules section 15 treats retrieved, hot-state and user-supplied content as data, never
    instructions, once it reaches an LLM prompt; wrapping it here in a plain-text, kind-labelled
    delimiter is how `render` keeps that boundary visible to whatever reads the assembled prompt.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: SectionLabel = Field(description="What kind of durable state this section carries.")
    text: str = Field(description="The section's rendered text, not yet delimited.")

    def delimited(self) -> str:
        """Wrap this section in its `<<<label>>> ... <<<end label>>>` plain-text delimiter."""
        tag = self.label.value
        return f"<<<{tag}>>>\n{self.text}\n<<<end {tag}>>>"


@functools.cache
def load_prompt(name: PromptName) -> str:
    """Read one prompt asset's full markdown text from the installed package.

    Args:
        name: Which prompt to load.

    Returns:
        The prompt file's contents, unmodified. Cached: the file never changes at runtime, so
        repeated calls for the same `name` read it once per process.

    Raises:
        PromptNotFoundError: No file for `name` ships in the installed package (a packaging bug,
            since every `PromptName` should have a matching `<name>.md`).
    """
    try:
        # importlib.resources, never a path relative to __file__: works the same from a wheel.
        resource = files(_PROMPTS_PACKAGE).joinpath(f"{name.value}.md")
        return resource.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise PromptNotFoundError(name) from exc


def render(name: PromptName, *, sections: Mapping[SectionLabel, str]) -> str:
    """Assemble one prompt's body plus its labelled sections, in stable-prefix order.

    Args:
        name: Which prompt body to load.
        sections: Section text keyed by label. Any subset of `SectionLabel` may be supplied; a
            label with no entry is simply omitted. The mapping's own iteration order does not
            matter -- the sections always come out ordered PINS, HOT_STATE, RETRIEVED, USER, EVENT
            (codingrules 8.9, "stable prefix first").

    Returns:
        The prompt body, then every supplied section's delimited text in stable-prefix order,
        each separated by a blank line.
    """
    # The shipped files end in a trailing newline; stripping it first keeps exactly one blank
    # line between the body and the first section instead of an extra one from the file itself.
    body = load_prompt(name).rstrip("\n")
    ordered_sections = [
        LabelledSection(label=label, text=sections[label]).delimited()
        for label in _SECTION_ORDER
        if label in sections
    ]
    return "\n\n".join([body, *ordered_sections])
