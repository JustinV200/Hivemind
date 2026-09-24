"""Define BrowserProcedure and export_procedure: a verified recording's browser work, made reusable.

ADR-0032, "Reusable browser work is rehearsed": a verified recording can be exported as a
`BrowserProcedure`, its typed steps and structural postconditions action by action, then replayed
against a fixture or staging site; the rehearsal's report is what the Royal Jelly Lab (9.3) asks
for before a procedure becomes a tool. Only VERIFIED actions are exported: a rejected or rolled
back attempt is the bee's mistake, not part of the procedure. Every step must be a browser step
(a desktop click has no page to replay against) and every postcondition structural (URL_MATCHES,
ELEMENT_TEXT), since a REGION_CHANGED digest belongs to one screen at one moment. A procedure
starts by navigating, so it can be replayed from nothing; the origin of that first page is where it
was recorded. A step the bee typed as secret was recorded as the mask; it becomes a `SecretSlot` a
rehearsal fills from values it is handed, so a procedure never holds a secret. `rebase` moves a
procedure from the origin it was recorded on to another (a fixture or staging copy of the site).

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside
    `hivemind.exoskeleton.rehearsal`. Called by the `hive recordings export` command and read by
    `rehearsal.rehearse`. Calls into `hivemind.exoskeleton.errors`, `.recorder` (RecordedAction),
    `hivemind.supervision.capping` (ACCEPTANCE_GUI_KINDS, BROWSER_OPS, ProposalState) and waggle.

Key invariants:
    - A BrowserProcedure never holds a secret: a secret step's text is the recorder's mask, and
      its value arrives only at rehearsal, by slot name.
    - Every step is a browser step, every postcondition URL_MATCHES or ELEMENT_TEXT, and the first
      step navigates.

See Also:
    - hivemind.exoskeleton.rehearsal.rehearse for the replay.
    - docs/adr/0032-gui-actions-are-capped-recorded-and-rolled-back-by-checkpoint.md.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Sequence
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field

from hivemind.exoskeleton.errors import ProcedureError
from hivemind.exoskeleton.recorder import RecordedAction, RecordedPostcondition
from hivemind.supervision.capping import ACCEPTANCE_GUI_KINDS, BROWSER_OPS, ProposalState
from waggle.messages.capping import GuiOp, GuiStep
from waggle.messages.labels import Postcondition, PostconditionKind

MAX_PROCEDURE_ACTIONS = 64  # A procedure is a task's worth of clicks, not a crawl.
NAME_PATTERN = r"^[a-z][a-z0-9_-]{0,63}$"  # A procedure's and a secret slot's name.
_WEB_SCHEMES = frozenset({"http", "https"})  # The only origins a procedure can be moved between.

__all__ = [
    "MAX_PROCEDURE_ACTIONS",
    "NAME_PATTERN",
    "BrowserProcedure",
    "ProcedureAction",
    "SecretSlot",
    "export_procedure",
    "origin_of",
    "rebase",
]

_FROZEN = ConfigDict(frozen=True, extra="forbid")


class SecretSlot(BaseModel):
    """Where a procedure needs a secret it does not hold: which step, and what to call it."""

    model_config = _FROZEN

    name: str = Field(pattern=NAME_PATTERN, description="What a rehearsal's value is keyed by.")
    action: int = Field(ge=0, description="The action the secret step is in.")
    step: int = Field(ge=0, description="The step within that action.")
    field: str = Field(description="The element the value is typed into, for a person to read.")


class ProcedureAction(BaseModel):
    """One verified GUI proposal: its steps, then the postconditions that proved it."""

    model_config = _FROZEN

    steps: tuple[GuiStep, ...] = Field(min_length=1, description="Browser steps, in order.")
    postconditions: tuple[Postcondition, ...] = Field(
        default=(), description="URL_MATCHES and ELEMENT_TEXT checks that held when recorded."
    )


class BrowserProcedure(BaseModel):
    """A verified recording's browser work, replayable without the bee that did it."""

    model_config = _FROZEN

    name: str = Field(pattern=NAME_PATTERN, description="The procedure's name.")
    recording_id: str = Field(description="The flight recording it was exported from.")
    origin: str = Field(description="scheme://host[:port] of the page it starts on.")
    actions: tuple[ProcedureAction, ...] = Field(min_length=1, max_length=MAX_PROCEDURE_ACTIONS)
    secrets: tuple[SecretSlot, ...] = Field(default=(), description="Values it is handed.")


def export_procedure(
    name: str, recording_id: str, actions: Sequence[RecordedAction]
) -> BrowserProcedure:
    """Export the VERIFIED actions of one recording as a BrowserProcedure.

    Args:
        name: What to call it (lowercase letters, digits, '-' and '_').
        recording_id: The recording `actions` came from.
        actions: That recording's actions, in the order they were recorded.

    Returns:
        The procedure: every verified action's steps and postconditions, secrets as slots.

    Raises:
        ProcedureError: Nothing was verified, a step is not a browser step, a postcondition is
            not structural, or the first verified step does not navigate.
    """
    verified = [action for action in actions if action.state == ProposalState.VERIFIED.value]
    if not verified:
        raise ProcedureError("the recording has no verified action to export")
    built = tuple(_action(index, action) for index, action in enumerate(verified))
    first = built[0].steps[0]
    if first.op is not GuiOp.NAVIGATE or first.url is None:
        raise ProcedureError("its first step must navigate, so a rehearsal can start from nothing")
    return BrowserProcedure(
        name=name,
        recording_id=recording_id,
        origin=origin_of(first.url),
        actions=built,
        secrets=tuple(_secret_slots(built)),
    )


def origin_of(url: str) -> str:
    """Return `url`'s scheme://host[:port]; a file:// URL's origin is just "file://"."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), "", "", ""))


def rebase(procedure: BrowserProcedure, origin: str) -> BrowserProcedure:
    """Move `procedure` onto `origin`: every navigation and URL check on its own origin follows.

    Args:
        procedure: A procedure recorded on an http(s) origin.
        origin: Where to rehearse it, e.g. "http://127.0.0.1:8000" for a fixture copy.

    Returns:
        The same procedure with its own origin replaced wherever a URL used it; URLs on any other
        origin are left alone (a rehearsal then refuses to navigate to them).

    Raises:
        ProcedureError: Either origin is not http(s), or `origin` carries a path.
    """
    target = origin_of(origin)
    if (
        urlsplit(origin).path not in ("", "/")
        or not _is_web(target)
        or not _is_web(procedure.origin)
    ):
        raise ProcedureError(f"only an http(s) procedure moves between http(s) origins: {origin}")

    def move(url: str) -> str:
        return target + url[len(procedure.origin) :] if _on(url, procedure.origin) else url

    actions = tuple(_moved(action, move) for action in procedure.actions)
    return procedure.model_copy(update={"origin": target, "actions": actions})


def _action(index: int, recorded: RecordedAction) -> ProcedureAction:
    """Rebuild one verified action from its recording; refuse what cannot be replayed."""
    if not recorded.gui:
        raise ProcedureError(f"action {index + 1} kept no typed steps to replay")
    for step in recorded.gui:
        if step.op not in BROWSER_OPS:
            raise ProcedureError(
                f"action {index + 1} has a {step.op.value} step, not a browser one"
            )
    checks = tuple(_postcondition(index, pc) for pc in recorded.postconditions)
    return ProcedureAction(steps=recorded.gui, postconditions=checks)


def _postcondition(index: int, recorded: RecordedPostcondition) -> Postcondition:
    """Rebuild one recorded postcondition, refusing any kind a page cannot show again."""
    kind = PostconditionKind(recorded.kind)
    if kind not in ACCEPTANCE_GUI_KINDS:
        raise ProcedureError(f"action {index + 1} is checked by {kind.value}, not by the page")
    return Postcondition(kind=kind, subject=recorded.subject, argv=(), expected=recorded.expected)


def _secret_slots(actions: Sequence[ProcedureAction]) -> Iterator[SecretSlot]:
    """Name every secret step after the field it fills, unique within the procedure."""
    used: set[str] = set()
    for action_index, action in enumerate(actions):
        for step_index, step in enumerate(action.steps):
            if not step.secret:
                continue
            name = _unique(_field_name(step) or f"secret_{len(used) + 1}", used)
            used.add(name)
            field = step.target.describe() if step.target is not None else "the focused field"
            yield SecretSlot(name=name, action=action_index, step=step_index, field=field)


def _field_name(step: GuiStep) -> str | None:
    """'password' for a field labelled or named 'Password'; None when nothing names it."""
    target = step.target
    label = (target.label or target.name) if target is not None else None
    slug = re.sub(r"[^a-z0-9]+", "_", (label or "").lower()).strip("_")[:48]
    return slug if slug and slug[0].isalpha() else None


def _unique(name: str, used: set[str]) -> str:
    """`name`, or `name_2`, `name_3`... when a slot already has it."""
    candidate, n = name, 1
    while candidate in used:
        n += 1
        candidate = f"{name}_{n}"
    return candidate


def _moved(action: ProcedureAction, move: _Mover) -> ProcedureAction:
    """Apply `move` to every navigation URL and URL_MATCHES expectation in one action."""
    steps = tuple(
        step.model_copy(update={"url": move(step.url)}) if step.url is not None else step
        for step in action.steps
    )
    checks = tuple(
        pc.model_copy(update={"expected": move(pc.expected)})
        if pc.kind is PostconditionKind.URL_MATCHES and pc.expected is not None
        else pc
        for pc in action.postconditions
    )
    return ProcedureAction(steps=steps, postconditions=checks)


def _on(url: str, origin: str) -> bool:
    """Whether `url` is on `origin` (same scheme and host:port), not merely prefixed by it."""
    return origin_of(url) == origin


def _is_web(origin: str) -> bool:
    """Whether `origin` is an http(s) one."""
    return urlsplit(origin).scheme in _WEB_SCHEMES


type _Mover = Callable[[str], str]
