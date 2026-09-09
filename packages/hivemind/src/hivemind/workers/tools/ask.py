"""Implement ask: raise a blocking Question up the chain and return its Answer as tool-result text.

Codingrules section 15: "If you are missing information... ask by raising a question up the chain
instead of guessing" (the Drone's own system prompt, `hivemind.llm.prompts.drone_system.md`, says
the same thing to the model). `ask` builds a `waggle.messages.supervision.Question` -- its id minted
fresh so it survives re-wrapping at every hop, its `task_id` and `clearance` taken from the current
assignment -- and awaits `ctx.asker.ask(question)`, which blocks the calling coroutine (and so the
whole tool loop) until a matching `Answer` arrives. There is no Proposal here: asking a question has
no side effect the Capping gate needs to check.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.tools`. Registered by
    `hivemind.workers.tools.registry.build_registry`. Calls into `hivemind.llm`,
    `hivemind.workers.tools.registry` and waggle only.

Key invariants:
    - `question.question_id` is minted here, from `ctx.clock`, never reused from any envelope id
      (`waggle.messages.supervision.questions`'s own rule).
    - The returned text always includes the chosen option's own text, not just its index, when the
      Answer names one, so the model reads a self-contained result without cross-referencing the
      Question it asked.

See Also:
    - .claude/codingrules.md section 15 for "ask... instead of guessing."
    - waggle.messages.supervision.questions for Question and Answer, the two shapes this module
      builds and reads.
    - hivemind.workers.context for QuestionChannel, the protocol `ctx.asker` satisfies.
"""

from __future__ import annotations

from hivemind.llm import JsonObject, ToolDefinition
from hivemind.workers.tools.registry import ToolInvocation, ToolSpec
from waggle.ids import new_message_id
from waggle.messages.supervision import Question

MAX_OPTIONS_ACCEPTED = 16  # Matches waggle.messages.supervision.questions.MAX_OPTIONS.

ASK_DEFINITION = ToolDefinition(
    name="ask",
    description=(
        "Ask a question up the chain and block until it is answered. Use this when information, "
        "credentials or a decision only a human can make are missing."
    ),
    parameters={
        "type": "object",
        "properties": {"text": {"type": "string"}, "options": {"type": "array"}},
        "required": ["text"],
        "additionalProperties": False,
    },
)

__all__ = ["ASK_DEFINITION", "ASK_SPEC", "ask"]


async def ask(invocation: ToolInvocation, arguments: JsonObject) -> str:
    """Raise `arguments["text"]` as a blocking Question and return its Answer as text.

    Args:
        invocation: This attempt's context and assignment.
        arguments: `text` (required) and `options` (optional, a list of choices).

    Returns:
        A readable string for a malformed `text`; otherwise the Answer's text, with the chosen
        option's own wording appended when the Answer names one.
    """
    text = arguments.get("text")
    if not isinstance(text, str) or not text:
        return "text must be a non-empty string."
    ctx = invocation.ctx
    options = _coerce_options(arguments.get("options"))
    question = Question(
        question_id=new_message_id(ctx.clock),
        task_id=invocation.assignment.task_id,
        asked_by=ctx.worker_id,
        text=text,
        options=options,
        clearance=invocation.assignment.clearance,
        asked_at=ctx.clock.now(),
    )
    # External await: blocks on the Warden relaying a human or Warden Answer back; no timeout
    # here because a blocked task is the intended, visible state (hive inbox surfaces it), not a
    # transient failure to retry.
    answer = await ctx.asker.ask(question)
    if answer.chosen_option is not None and answer.chosen_option < len(options):
        chosen = options[answer.chosen_option]
        return f"{answer.text} (chose option {answer.chosen_option}: {chosen!r})"
    return answer.text


def _coerce_options(value: object) -> tuple[str, ...]:
    """Return `value` as a bounded tuple of strings, or an empty tuple when it is not a list."""
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value[:MAX_OPTIONS_ACCEPTED])


ASK_SPEC = ToolSpec(definition=ASK_DEFINITION, run=ask)
