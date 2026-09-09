# ADR-0008: LLM provider independence and model slots

- Status: Accepted
- Date: 2026-09-08

## Context

The Hive starts on a hosted model (Claude) and the README's stated bet is that it must be able to
move, slot by slot, to a locally hosted model without touching any code above `hivemind.llm`:
Requeening (recovering a crashed Queen), Clustering (pausing bees while a provider is down,
codingrules section 8.13) and the whole degrade-gracefully posture of the system depend on no
subsystem knowing, at the type level, which vendor answers a call. Phase 3 is the first phase that
builds real Workers, a Warden and a Queen that all think with a model, so the boundary they call
through has to be fixed before any of them exist, not discovered by refactoring three call sites
later. Two concrete forces shape the boundary: first, a weak local model (a small `openai_compat`
server with no native tool-call protocol and no JSON-schema enforcement) has to work through the
same interface a strong hosted model does, which means the interface cannot assume any one
capability is always present; second, codingrules section 4 fixes `llm` as a Layer 1 package that
may depend on `forage` (for `ModelSlot`, `Effort` and, once phase 3 step 3.12 lands, the Forage
map's costs) but never the reverse, and `forage` must not import `hivemind.llm` even transitively,
which rules out `hivemind.llm.slots.BoundModel` holding a live reference to a `forage`-defined cost
type while `forage`'s own cost shape (`ModelCost`) is still being written by a parallel dispatch
this same phase. Every provider adapter (`llm/providers/anthropic/`, `llm/providers/openai_compat/`)
is also a separate, later dispatch; this decision has to name the seam they build against before
either one exists, and has to name it in a way a fake implementation can satisfy just as honestly
as a real one, so the whole system above `hivemind.llm` is testable with no network and no API key.

## Decision

`hivemind.llm` is the one door every model call in the Hive passes through, exactly as codingrules
section 8.6 requires: `LLMProvider` (`llm/provider.py`) is a `typing.Protocol` with `name`,
`capabilities`, `complete`, `stream`, `count_tokens` and `health`, and no code outside
`llm/providers/<name>/` may import a vendor SDK (enforced by `lint-imports`, section 4). Every
request and response crossing that door is one of HiveMind's own frozen pydantic types
(`llm/models.py`: `LLMRequest`, `Message`, the `ContentPart` discriminated union, `ToolDefinition`,
`ToolCall`, `LLMResponse`, `Usage`, `LLMChunk`) with `extra="forbid"`, so a vendor's JSON shape
never leaks a field upward by accident; each adapter's own `mapping.py` (a later dispatch) is the
only place a vendor field name is allowed to appear. Every provider declares a frozen
`ProviderCapabilities` (`llm/capabilities.py`) up front -- native tool calls, schema-enforced
output, JSON mode, vision, streaming, reasoning control, context window, system-role support,
parallel tool calls, token counting -- and core code branches on that declared set, never on
`provider.name`; `ProviderCapabilities.full()` and `.none()` are the two ends the degradation
ladders (`llm/ladders/`, roadmap step 3.5) are proven against, so a plain-text local model gets a
working interface through prompted fallbacks rather than a missing feature. `ModelSlot` and
`Effort` live in `hivemind.forage.slots`, not here (roadmap step 3.4, landed ahead of this step by
the orchestrator per the phase 3 brief), so autopilot and Forage can name a slot without importing
provider machinery; `hivemind.llm.slots.BoundModel` is the value a later `resolve(slot, manifest)`
(roadmap step 3.4's `llm` half, a separate dispatch) returns, carrying the slot, the manifest
binding key, a live `LLMProvider` instance, a model id, an effort, a context window, an optional
fallback `BoundModel`, and its per-million-token prices as two plain floats
(`cost_per_million_input_usd`/`cost_per_million_output_usd`) copied from the Forage map at bind
time -- plain floats, not a `hivemind.forage.models.ModelCost` reference, specifically so `llm`
never has to import a `forage` submodule that both does not exist yet this phase and would, if it
ever held a live back-reference, invite the exact `forage -> llm` cycle section 4 forbids. `[llm]
offline = true` refuses any provider whose base URL is not loopback (`OfflineViolationError`),
proving a Hive can run with no external network. `Usage` is the one normalised token-and-cost
shape every response carries, with a cost-aware `__add__` (a sum is unpriced, `None`, the moment
either addend is), so the Pheromone Trail and the cost view never depend on which provider ran.
`llm/fake.py`'s `FakeLLMProvider` is a fully honest implementation of the same Protocol: it drops
`ToolCallPart`s it was scripted to return when `native_tool_calls` is false, only counts tokens
when `token_counting` is true, and raises the same typed `ProviderUnavailableError`
(`llm/errors.py`'s `LLMError` tree) a real outage would, so every layer above `hivemind.llm` is
exercised against the same error and capability shapes a real provider produces.

## Consequences

Positive: a new provider adapter is a `mapping.py` plus a class implementing six protocol members,
never a change to a Worker, a Warden, the Queen or a ladder; moving a `ModelSlot` from a hosted API
to a local server is a manifest edit (`[llm.slots]`), not a code change, which is the README's
literal promise. Declaring capabilities instead of branching on provider name means a new, weaker
provider slots in immediately wherever the ladders already handle `ProviderCapabilities.none()`,
with no new `if provider.name == ...` anywhere to add or forget. `FakeLLMProvider`'s honesty about
capabilities means a test that exercises a Drone or a ladder against `.none()` is proving the real
degradation path, not a shortcut that happens to pass. Keeping `BoundModel`'s cost as two floats
rather than a `forage` type lets this dispatch and the parallel Forage dispatch (`forage/models/`,
roadmap step 3.12) proceed without either blocking on the other, and keeps the `llm -> forage`
edge one-directional forever, not just until someone adds a field.

Negative: two plain floats duplicate a shape `forage.models.ModelCost` will also carry once it
lands, so a future dispatch (`resolve`, roadmap step 3.4's remaining half) has to keep the copy in
sync by hand at bind time rather than sharing one type; if `ModelCost` grows fields `BoundModel`
would benefit from (a currency, a per-request minimum), `BoundModel` does not get them for free.
A Protocol with no shared base implementation means every adapter re-derives its own retry and
timeout handling inside `complete`/`stream`, though the ladders and the Fanner (a later roadmap
step) are exactly where that common behaviour is meant to live instead, once they exist. Declaring
`stream` as a plain `def` returning `AsyncIterator[LLMChunk]` (rather than `async def`) so an
implementation may be an ordinary async generator function is a subtler signature than most of the
codebase's protocols, and a contributor unfamiliar with the trick may reach for `async def` with a
`return` instead of `yield`, which changes the calling convention (the caller would need to
`await` the call before iterating) and would fail the provider contract suite (roadmap step 3.8)
the moment it is added.

## Alternatives considered

A LangChain- or LiteLLM-style universal abstraction: buys instant support for many providers, but
imports its own request/response/tool shapes and its own capability model, which would put a
third party's design decisions at the center of codingrules section 8.6's boundary and make the
degradation ladders' behaviour depend on that library's own (undocumented, in HiveMind's terms)
capability handling rather than the Hive's own `ProviderCapabilities`.

Vendor SDK types at the boundary (an `anthropic.types.Message` or an OpenAI `ChatCompletion`
passed upward from `complete`): saves writing `llm/models.py` and each adapter's `mapping.py`, but
means every subsystem above `hivemind.llm` would need to special-case which vendor's shape it
received, which is precisely the coupling this ADR exists to prevent, and would make a local-model
migration a type-level change everywhere a response is read, not a manifest edit.

Branching on `provider.name` instead of `ProviderCapabilities`: cheaper to write for the first two
providers, but it is a review rejection under codingrules section 8.6 for good reason -- it forces
every new provider to be taught to every `if` chain that checks a name, rather than declaring what
it can do once and letting existing code adapt automatically.
