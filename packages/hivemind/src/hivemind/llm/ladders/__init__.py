"""Provide the degradation ladders: complete_structured and run_tool_loop (roadmap step 3.5).

Codingrules section 8.6 ("degrade by ladder, in one place") is the rule this package makes
concrete for the two shapes of model output every bee needs: a structured decision
(`structured.complete_structured`) and a tool-calling conversation (`tools.run_tool_loop`). Both
walk the same three ideas, split across this package's five modules: a **rung** or **protocol**
chosen from the bound provider's declared `ProviderCapabilities` (never its name); a **retry**
within that rung/protocol, using a validation error to help the model correct itself
(`extraction`); a **fallback** to the next binding in the chain on an outage
(`hivemind.llm.errors.ProviderUnavailableError`/`RateLimitedError`), made through a `CallGate`
seam (`gate`) so a later metering layer (the Fanner, roadmap step 3.12a) can sit behind every call
without either ladder knowing; and a step-down or fallback reported through a `LadderObserver`
(`observer`), so the Pheromone Trail records every degradation without either ladder writing a
trail event by hand.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm`. Called by every
    awake episode and Worker tool loop that needs a model's reply shaped a particular way (later
    roadmap steps: 3.16, 3.19, 3.20). Calls into `hivemind.llm`'s other modules, `hivemind.forage`
    and `hivemind.pheromone` only; no vendor SDK is imported here or anywhere below `hivemind.llm`
    except `llm/providers/<name>/`.

Key invariants:
    - `ContextTooLongError` is never caught by either ladder: it always propagates to the caller,
      which owns the prompt budget (codingrules section 8.6).
    - `RefusedError` is raised by the ladder itself, from a response's `stop_reason`, never by a
      provider directly.
    - A ladder called with no `CallGate` uses `DirectCallGate` (no metering); a ladder called with
      no `LadderObserver` uses `NullLadderObserver` (discards every note).

See Also:
    - .claude/codingrules.md section 8.6 for "degrade by ladder, in one place".
    - docs/adr/0009-structured-output-and-tool-call-degradation-ladders.md for the decision this
      package implements.
    - hivemind.llm for the boundary (LLMProvider, BoundModel) these ladders are built on.

Public API:
    - Structured output (`hivemind.llm.ladders.structured`): `complete_structured`,
      `StructuredResult`, `Rung`, `NATIVE_SCHEMA_RETRIES`, `JSON_MODE_RETRIES`,
      `PROMPTED_JSON_RETRIES`.
    - Tool calls (`hivemind.llm.ladders.tools`): `run_tool_loop`, `ToolExecutor`,
      `ToolLoopOptions`, `ToolLoopResult`, `MAX_TOOL_ROUNDS_DEFAULT`.
    - Argument validation (`hivemind.llm.ladders.extraction`): `validate_arguments`.
    - The call seam (`hivemind.llm.ladders.gate`): `CallGate`, `DirectCallGate`.
    - Reporting a step-down (`hivemind.llm.ladders.observer`): `LadderObserver`, `FallbackNote`,
      `FallbackReason`, `NullLadderObserver`, `TrailLadderObserver`.
"""

from hivemind.llm.ladders.extraction import validate_arguments
from hivemind.llm.ladders.gate import CallGate, DirectCallGate
from hivemind.llm.ladders.observer import (
    FallbackNote,
    FallbackReason,
    LadderObserver,
    NullLadderObserver,
    TrailLadderObserver,
)
from hivemind.llm.ladders.structured import (
    JSON_MODE_RETRIES,
    NATIVE_SCHEMA_RETRIES,
    PROMPTED_JSON_RETRIES,
    Rung,
    StructuredResult,
    complete_structured,
)
from hivemind.llm.ladders.tools import (
    MAX_TOOL_ROUNDS_DEFAULT,
    ToolExecutor,
    ToolLoopOptions,
    ToolLoopResult,
    run_tool_loop,
)

__all__ = [
    "JSON_MODE_RETRIES",
    "MAX_TOOL_ROUNDS_DEFAULT",
    "NATIVE_SCHEMA_RETRIES",
    "PROMPTED_JSON_RETRIES",
    "CallGate",
    "DirectCallGate",
    "FallbackNote",
    "FallbackReason",
    "LadderObserver",
    "NullLadderObserver",
    "Rung",
    "StructuredResult",
    "ToolExecutor",
    "ToolLoopOptions",
    "ToolLoopResult",
    "TrailLadderObserver",
    "complete_structured",
    "run_tool_loop",
    "validate_arguments",
]
