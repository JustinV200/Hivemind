# ADR-0009: structured output and tool-call degradation ladders

- Status: Accepted
- Date: 2026-09-08

## Context

ADR-0008 fixes the LLM boundary (`LLMProvider`, `ProviderCapabilities`, `BoundModel`) so any code
above `hivemind.llm` can call a model without knowing which vendor answers, but it deliberately
stops short of saying *how* a caller gets a schema-shaped decision or a tool call out of a model
whose capabilities vary from "native schema output, native tool calls, parallel calls allowed" all
the way down to `ProviderCapabilities.none()` -- a plain-text model with nothing but a chat
completion. Every awake episode (`queen/awake`, `wardens/awake`, roadmap steps 3.19-3.20) needs a
structured decision back; every Worker's tool loop (the Drone, roadmap step 3.16) needs to call
tools whether or not the bound provider has a native tool-call protocol. Codingrules section 8.6
already names the shape this has to take ("degrade by ladder, in one place... a weaker local model
gets the same interface with more retries and the retry counts are commented constants") and lists
`llm/structured.py`/`llm/tools.py` as the modules; this phase's dispatch map instead places them
under `llm/ladders/` as a package, because the ladder logic itself -- rung selection, retries,
fenced-block extraction and validation, fallback-chain handling, and reporting a step-down to the
Pheromone Trail -- is more than two modules' worth of one concept each (codingrules 5.2). Two
further forces shape the design: the Fanner (roadmap step 3.12a, a later dispatch) has to be able
to meter every call a ladder makes without either ladder knowing it exists, and a tool call
proposed by a model is untrusted input (codingrules section 15) on every protocol, not just the
prompted one, so validation cannot live only in the prompted-parsing path.

## Decision

Two ladders, `complete_structured` (`llm/ladders/structured.py`) and `run_tool_loop`
(`llm/ladders/tools.py`), share one shape built from three seams in `llm/ladders/`'s other three
modules. A **rung** (structured output only: `Rung.NATIVE` -> `JSON_MODE` -> `PROMPTED`) or a
**protocol** (tool calls: native or prompted) is chosen from the bound provider's declared
`ProviderCapabilities`, never its name, exactly as ADR-0008 requires. Within one rung or protocol,
a validation failure retries up to a named, commented constant
(`NATIVE_SCHEMA_RETRIES = 1`, `JSON_MODE_RETRIES = 2`, `PROMPTED_JSON_RETRIES = 3`), carrying the
validation error back as a new user message so the model can correct itself; when a rung's retries
are exhausted, `complete_structured` steps down to the next weaker rung rather than giving up, and
raises `MalformedOutputError` only once PROMPTED itself is exhausted. When the gate raises
`ProviderUnavailableError` or `RateLimitedError`, the whole ladder moves to `bound.fallback` (when
one exists) and restarts fresh at the fallback's own top rung -- a rung step-down and a binding
fallback are deliberately different kinds of event, never conflated, which is why `FallbackNote`
(`llm/ladders/observer.py`) leaves `to_binding` `None` for the former and `from_rung`/`to_rung`
both `None` for the latter. `ContextTooLongError` is never caught by either ladder: it always
propagates to the caller, which owns the prompt budget (`hivemind.memory.assemble`, a parallel
dispatch) and must shrink it and retry; a ladder cannot know how to shrink a request it did not
assemble. `RefusedError` is raised by the ladder itself, from a response's `stop_reason ==
REFUSAL`, never by a provider directly, keeping "the model declined" and "the call failed" as the
same error family a caller catches once.

Every call, on either ladder and either tool-call protocol, is made through `CallGate`
(`llm/ladders/gate.py`), a one-method Protocol (`complete(bound, request) -> LLMResponse`) whose
only implementation today is `DirectCallGate`, a pass-through with no behaviour of its own. This is
the seam the Fanner will occupy once it exists: it implements the same Protocol, and every call a
ladder makes becomes seat-metered by changing what the composition root passes in, never by
changing either ladder. Every step-down or fallback is reported through `LadderObserver`
(`llm/ladders/observer.py`), a one-method Protocol (`on_fallback(note)`) with two implementations:
`NullLadderObserver` (discards, the default) and `TrailLadderObserver`, which records an
`llm.fallback` `LlmEvent` on the Pheromone Trail with a payload of ids and enum values only --
`from_binding`, `to_binding`, `from_rung`, `to_rung`, `reason` -- never text, consistent with
codingrules section 12. `LlmEvent.subject_id` must name a well-formed waggle id, but a ladder call
has no id of its own to point at yet (no `CallId` `IdKind` exists), so `TrailLadderObserver` mints
a fresh `EventId` as a stand-in handle for the occurrence, distinct from the recorded event's own
`id`; a later dispatch that gives a call a real id (most plausibly the Fanner, since it already
needs to correlate a call end-to-end for metering) can replace this without changing the payload
shape.

Tool-call argument validation (`validate_arguments(schema, arguments) -> tuple[str, ...]`,
`llm/ladders/extraction.py`) runs on every call before it reaches `ToolExecutor.execute`, on both
the native and the prompted protocol -- native tool calls are just as much untrusted model output
as a parsed fenced block, so there is exactly one validation path, not one per protocol. It
implements a small, documented JSON-schema subset (`type` for the six JSON types, `properties`,
`required`, `additionalProperties: false`, `enum`, checked at the top level and one level into
`properties`) rather than depending on the `jsonschema` package. An invalid call is never executed;
its errors travel back to the model as an `is_error` `ToolResultPart`, so a model that got its own
arguments wrong gets to see why and try again, on the model's own next turn -- the same "untrusted
input, corrective feedback" shape the retry-with-validation-error mechanism already uses for
structured output.

## Consequences

Positive: a caller (an awake episode, the Drone) never learns which rung or protocol actually ran;
the same call site produces a working result whether it is bound to the strongest hosted model or
`ProviderCapabilities.none()`, exactly as codingrules section 8.6 promises, and every capability
level is provable in tests against `FakeLLMProvider` alone, no network required. Wiring in the
Fanner later is a composition-root change (pass a `Fanner` instance as `gate=`) rather than a
change to either ladder's own control flow, because `CallGate` already sits between every call and
`bound.provider.complete`. The rung/binding split in `FallbackNote` keeps the Pheromone Trail's
`llm.fallback` events unambiguous to a human or dashboard reading them: "this binding got weaker"
and "this binding is unreachable" are never the same row shape.

Negative: `TrailLadderObserver` minting a fresh `EventId` for `subject_id` means an `llm.fallback`
event's subject is not, today, joinable to any other row on the trail (a Task, a Worker, a Cell);
a human auditor sees that a fallback happened and why, but not yet "for which task" without cross
referencing by timestamp. This is flagged in this dispatch's report rather than solved here,
because the fix (a real call-scoped id) belongs to whichever dispatch first needs to correlate a
call end-to-end, most plausibly the Fanner. `validate_arguments`'s schema subset does not recurse
past one level of `properties`, so a tool whose arguments contain a nested object with its own
required fields gets no validation on that inner shape; a tool author who needs that either flattens
the schema or accepts the gap until a future step widens the subset (or a real validator is judged
worth the dependency after all).

## Alternatives considered

Per-provider retry code (each adapter handles its own structured-output and tool-call fallback):
rejected by codingrules section 8.6 directly ("degrade by ladder, in one place... never in an
adapter") -- it would mean every new provider re-derives the same three rungs, and a bug fix in the
retry logic would need to land in every adapter rather than once.

A `jsonschema` dependency for `validate_arguments`: buys full JSON-schema coverage (recursive
`properties`, `oneOf`, `pattern`, numeric bounds) at the cost of a new third-party dependency for a
narrow use case -- a tool's arguments are almost always a flat object of scalars, and the subset
this ADR implements covers that shape exactly, with unsupported keywords silently ignored rather
than rejected, so a stricter schema still round-trips through this validator without becoming
invalid.

Trusting native output without validation (a provider that declares `schema_output` or
`native_tool_calls` is assumed to always honour it): rejected by codingrules section 15 ("LLM
output is untrusted input... tool calls proposed by a model are validated against the tool's
schema and the Worker's capabilities before execution") -- a capability declaration says the
provider *can* do a thing, not that every individual response actually did it correctly, and a
native tool call is exactly as capable of naming an unoffered tool or missing a required argument
as a prompted one.
