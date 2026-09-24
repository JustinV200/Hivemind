"""Define ToolCallRecord and classify_error: one call's own record, and how it reads as pass/fail.

Every side-effecting tool this package's siblings offer (`write_file`, `run_command`,
`http_request`) goes through the Capping gate and reports back through
`hivemind.workers.tools.proposals.describe`, whose own rendering always starts with a fixed
`state=<VERIFIED|REJECTED|ROLLED_BACK>` token; every other built-in tool (`read_file`, `ask`, the
registry itself for an unknown tool or a schema violation) reports a failure through one of a
small, fixed set of literal templates. `classify_error` reads exactly those two vocabularies --
never a model's own free-form text -- to decide whether one call's result was a failure, and
`target_for` names the one path/command/url a call acted on, so a Handoff line can say what,
not just that something happened.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.drone.outcome`. Read by
    `hivemind.workers.roles.drone.outcome.executor` (to classify a call as it lands) and
    `hivemind.workers.roles.drone.outcome.fields` (to turn classified calls into Handoff lines).
    Calls into `hivemind.llm` and `hivemind.workers.tools.exoskeleton` (ACTION_TOOL_NAMES) only.

Key invariants:
    - `classify_error` never inspects `ask`'s own result text as an error signal: an Answer's
      wording is free-form human/Warden text, not one of the fixed templates this module matches.
    - `is_lasting` holds for every call to a tool in `SIDE_EFFECT_TOOLS` and for a GUI action
      flagged irreversible; a Handoff's `do_not_redo` (built in `fields.py`) names nothing else.
    - `target_for` never renders typed text: a GUI action is named by its element, URL or point.

See Also:
    - hivemind.workers.tools.proposals for describe(), the "state=" rendering this module parses.
    - hivemind.workers.tools.session and .http for the fixed error templates this module matches.
    - hivemind.workers.roles.drone.outcome.executor for _RecordingExecutor, this module's caller.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.llm import ToolCall
from hivemind.workers.tools.exoskeleton import ACTION_TOOL_NAMES

# Every tool that proposes a side effect through the Capping gate (hivemind.workers.tools.
# proposals.cap) and leaves it behind: the only ones a Handoff's do_not_redo list names without
# looking at the call, since nothing else this Drone offers changes anything outside the model's
# own next turn. (`keep` was missing until 2026-09-24: a resumed bee would have kept a file twice.)
SIDE_EFFECT_TOOLS = frozenset({"write_file", "run_command", "http_request", "keep"})

# hivemind.workers.tools.proposals.describe's own rendering always starts with one of these two
# tokens for a capped tool's result: reading them is parsing a fixed, machine-produced format,
# never matching on free-form prose (describe's own docstring: "never... the diff text back",
# only ids, the verdict and pass/fail).
_CAPPED_STATE_PREFIX = "state="
_CAPPED_VERIFIED_PREFIX = "state=VERIFIED"

# Every non-capped tool's own built-in error text is one of these fixed templates
# (hivemind.workers.tools.session/http/ask/registry); matched by exact prefix, never fuzzy prose
# matching, since a model's own answer text (from `ask`) never starts with one of these.
CAPABILITY_DENIAL_PREFIXES = ("no net capability covers ", "no fs:read capability covers ")
_KNOWN_ERROR_PREFIXES = (
    *CAPABILITY_DENIAL_PREFIXES,
    "no tool named ",
    "no file at ",
    "path must be a non-empty string.",
    "content must be a string.",
    "argv must be a non-empty list of strings.",
    "url must be a non-empty string.",
    "text must be a non-empty string.",
)

# describe()'s own checks=(...) segment names a failed check by kind; ALLOWLIST covers both the
# path and command allowlists, SIZE_CAP the diff size cap (codingrules 8.9: "path and command
# allowlists, diff size caps") -- the two check kinds this dispatch's constraints care about.
ALLOWLIST_FAILED_MARK = "ALLOWLIST=FAILED"
SIZE_CAP_FAILED_MARK = "SIZE_CAP=FAILED"

__all__ = [
    "ALLOWLIST_FAILED_MARK",
    "CAPABILITY_DENIAL_PREFIXES",
    "SIDE_EFFECT_TOOLS",
    "SIZE_CAP_FAILED_MARK",
    "ToolCallRecord",
    "classify_error",
    "is_lasting",
    "target_for",
]


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    """One tool call this attempt made, and what it returned.

    Not a conversation transcript (`scripts/check_no_transcripts.py` forbids a growing
    `messages`/`history` name outside `memory/`): a bounded, per-attempt list of these
    (`hivemind.workers.roles.drone.outcome.executor.MAX_RECORDED_CALLS`) is read exactly once, at
    checkpoint time, to derive a Handoff's guidance fields -- it is never replayed back to a model.

    Attributes:
        call: The validated call a model asked for.
        result_text: The text `hivemind.workers.tools.registry.ToolRegistry.execute` returned.
        is_error: Whether `result_text` reports a failure (`classify_error`'s own verdict).
    """

    call: ToolCall
    result_text: str
    is_error: bool


def classify_error(name: str, content: str) -> bool:
    """Return whether one tool call's result text reports a failure.

    Args:
        name: The tool's own name (`ToolCall.name`).
        content: The result text `hivemind.workers.tools.registry.ToolRegistry.execute` returned
            for that call.

    Returns:
        True when `content` reports a rejected/rolled-back proposal, or one of the built-in
        tools' own fixed error templates; False for anything else (a successful result, an
        answer `ask` returned, or file content `read_file` returned).
    """
    if content.startswith(_CAPPED_STATE_PREFIX):
        # A capped tool's own result: only VERIFIED means the proposal actually applied.
        return not content.startswith(_CAPPED_VERIFIED_PREFIX)
    if name in SIDE_EFFECT_TOOLS:
        # Reached before a Proposal was ever built (a malformed path/argv/url): always a failure.
        return True
    return content.startswith(_KNOWN_ERROR_PREFIXES)


def is_lasting(call: ToolCall) -> bool:
    """Whether `call`'s effect outlives this attempt, so a resuming bee must not repeat it.

    A capped side effect always does. A GUI action does only when the bee called it irreversible
    (a submitted form, a payment): a resumed bee is equipped afresh, with a new browser and
    screen, so every other GUI step is one it may well need to take again (ADR-0032).

    Args:
        call: One call that landed.

    Returns:
        True for a SIDE_EFFECT_TOOLS call or an irreversible GUI action.
    """
    if call.name in SIDE_EFFECT_TOOLS:
        return True
    return call.name in ACTION_TOOL_NAMES and call.arguments.get("irreversible") is True


def target_for(call: ToolCall) -> str:
    """Name the one path/command/url `call` acted on, for a short Handoff line.

    Args:
        call: The tool call to describe.

    Returns:
        The call's own path, its argv joined into one command line, or its url; the tool's own
        name when none of its arguments name a target at all.
    """
    if call.name in ("write_file", "read_file"):
        path = call.arguments.get("path")
        return str(path) if isinstance(path, str) else "<unknown path>"
    if call.name == "run_command":
        argv = call.arguments.get("argv")
        if isinstance(argv, list):
            return " ".join(str(part) for part in argv)
        return "<unknown command>"
    if call.name == "http_request":
        url = call.arguments.get("url")
        return str(url) if isinstance(url, str) else "<unknown url>"
    if call.name == "keep":
        source, destination = call.arguments.get("source"), call.arguments.get("destination")
        return f"{source} to {destination}" if isinstance(source, str) else "<unknown path>"
    if call.name in ACTION_TOOL_NAMES:
        return _gui_target(call)
    return call.name


def _gui_target(call: ToolCall) -> str:
    """Name what a GUI action acted on: its element, its URL or its point; never typed text."""
    arguments = call.arguments
    target = arguments.get("target")
    if isinstance(target, dict):
        named = ", ".join(
            f"{key}={value!r}" for key, value in target.items() if isinstance(value, str)
        )
        return named or call.name
    url = arguments.get("url")
    if isinstance(url, str):
        return url
    x, y = arguments.get("x"), arguments.get("y")
    return f"({x}, {y})" if isinstance(x, int) and isinstance(y, int) else call.name
