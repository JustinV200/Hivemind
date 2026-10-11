# ADR-0044: Every local model server is reached through the one OpenAI-compatible adapter; Ollama is managed by probe, Warden-hosted servers are declared in the manifest and reported as sources, local cost is amortised, and prompt overlays apply at the provider door

- Status: Accepted
- Date: 2026-10-11

## Context

Phase 8's goal is that "moving from Claude to locally hosted models is a configuration exercise:
every slot can run on a local model". Codingrules 8.6 already fixes the shape of that promise:
vendor SDKs and model-server HTTP clients live only in `llm/providers/<name>/`, capabilities are
declared and never inferred from a provider's name, a prompt tweak lives "in that adapter's
`mapping.py` or a per-provider overlay file under `llm/prompts/overlays/<provider>/`, never in
the shared prompt", and "usage is normalised": a `Usage` carries "cost when the manifest lists a
price for that model". Codingrules 8.10 adds that every model slot "has a cost: a seat on a
specific host, or hosted spend", that a Warden loads an allowlisted model within its VRAM ceiling
autonomously "and is reported as a new source, not requested", and that the Queen may evict a
local model "only to reclaim seats she has exported".

The roadmap steps this ADR covers say:

- 8.2: "`llm/providers/ollama/manage.py` (optional): list, pull, `keep_alive`, health. `hive
  doctor` uses it. Generic health probe covers vLLM and llama.cpp."
- 8.3: "start and stop an OpenAI-compatible server on a Cell that reports `can_host_model` (through
  its session), register it as a provider with a loopback-only or Cell-local base URL, add its
  models to the Forage map as new sources with measured distance from that Cell, and report its
  seats to the ledger as part of that Cell's local pool. ... The Cell's Warden loads and unloads
  models on it autonomously within its ceilings and allowlist, reporting each as
  `forage.source_added` / `removed`; the Queen may evict a model only to reclaim seats she has
  exported."
- 8.4: "add overlays under `llm/prompts/overlays/<provider>/` where needed, each with a header
  saying why."
- 8.8: "Zero or amortised prices in the manifest; usage from the response or a documented estimate;
  both uniform on the trail."

The forces:

- Ollama, vLLM, llama.cpp's server and LM Studio all speak the same `/v1/chat/completions`,
  `/v1/embeddings` and `/v1/models` routes. They differ in operations (pulling a model, keeping it
  loaded) and in quirks (whether `stream_options` is honoured, whether usage comes back).
- A local server reports no price and sometimes no usage. The trail's `llm.call` event requires a
  usage, and `mapping.py` today sets `cost_usd = None` for it.
- A server on a Cell is started by that Cell's Warden, through the Cell's `CellSession`, because
  the Warden owns the Cell. The Queen must still learn what it serves, because the shared ledger
  and her hosting plans are built from the Forage map.
- Phase 7's real runs showed the same prompt behaving differently on a 3B and a 7B model, so
  per-model prompt fixes will be needed, and must not leak into shared prompts or into call sites.
- Nothing above `hivemind.llm` may know which server answered (ADR-0008).

## Decision

**One adapter for every local server.** `llm/providers/openai_compat/` is the adapter for Ollama,
vLLM, llama.cpp, LM Studio and any hosted OpenAI-compatible API. Differences between servers are
declared as capabilities in `[llm.providers.<name>.capabilities]`, never branched on by name.
There is no per-server adapter.

**Management exists only for Ollama, and is detected by probing.** `llm/providers/ollama/manage.py`
offers list, pull, `keep_alive` and health. Whether a provider is Ollama is learned by probing its
native API (`/api/tags`) on the provider's own base URL, never by reading the provider's name or
the port, and a failed probe means "unmanaged". Every other server gets the generic `/models`
health probe the adapter already has. `hive doctor` and `hive llm providers` use the management
surface when the probe succeeds and report "unmanaged" otherwise. Nothing is required of a server
beyond the OpenAI-compatible routes.

**`[llm.servers]` declares a server a Warden may host.** A table `[llm.servers.<server_id>]` holds
the argv template, the port, the `[llm.providers]` name the server is registered as, the Forage
map source ids it may load, and a health path. The argv template is manifest data, so no server
command appears in code. The Cell's Warden starts it with `CellSession.start`, waits for health,
registers the provider at runtime with a loopback or Cell-local base URL, adds its models to the
Forage map as sources whose `host_cell_id` is its Cell, and tells the Queen with
`forage.source_added`. Stopping a model or the server sends `forage.source_removed`. The Queen
updates her map and the ledger's seat capacity from those reports, and may send
`forage.source_evict` only for seats she exported. Loading within `Ceilings.loadable_sources` and
`Ceilings.model_vram_bytes` is autonomous. `ModelServerReport.server_id`, already specified in
Waggle, is the `[llm.servers]` key. Weights are whatever the server finds on the Cell; shipping
them from the Basket waits for roadmap 9.2a.

**Local cost is amortised, and usage is estimated only when absent.** A local source's
`ModelCost.cost_per_seat_hour_usd` amortises hardware. A call's cost is its tokens times the
per-million prices plus the seat-hours it occupied times the per-seat-hour price, so zero stays the
default and an operator who wants real numbers sets one field. When a server returns no usage, the
adapter estimates tokens from characters using `CHARS_PER_TOKEN_ESTIMATE` and sets `Usage.estimated
= True`. Both the cost and the flag reach the trail's `LlmUsage` uniformly, so a local call and a
hosted call are the same event, and the cost view can say how much of a total is estimated.

**Prompt overlays apply at the provider door.** `llm/prompts/overlays/<overlay>/` holds replacement
or appended prompt text, each file with a header saying why it exists. A provider chooses its
overlay by `[llm.providers.<name>] prompt_overlay`, default none. The registry wraps the provider
when it builds it, so the overlay is applied once, in one place, to every request that provider
sees. The overlay's name is a manifest value, not the provider's kind or name, so two providers
that need the same fix share a directory and no code asks which provider it has.

## Consequences

Positive: a new OpenAI-compatible server needs a manifest entry and, if it differs, capability
overrides, never code. A Cell can become a model host with a manifest edit and a Warden that
already knows how. The Queen's ledger stays the single place seats are counted. Local calls
appear on the trail with a cost and an honesty flag, so `hive forage status` and the cost view need
no local special case. A prompt that works on a hosted model is never edited to suit a local one.

Negative: a server with a real quirk the capability flags cannot express (a wrong `finish_reason`,
a malformed stream) is fixed inside the shared adapter's `mapping.py`, so a bug fix for one server
can touch all of them, and the contract suite must carry a cassette per server family. An
estimate from characters is wrong by tens of percent for code or non-Latin text. An overlay is one
more thing to keep in step with the shared prompt; roadmap 8.4 runs the prompt snapshots against
local models for that reason. A Warden-started server is a long-running process on a Cell, so Cell cleanup
must stop it, which the `CellSession` contract already requires of anything it starts.

## Alternatives considered

A per-server adapter (`ollama/`, `vllm/`, `llamacpp/`, `lmstudio/` each with a full chat client):
four copies of one wire protocol, four sets of cassettes, and a provider-name branch somewhere to
choose between them, which codingrules 8.6 forbids. Only the part that really differs, Ollama's
management API, gets its own module.

Detecting Ollama by provider name or port: a renamed provider or a proxy on another port would
silently lose management, and the name check is the kind of branch the lint script exists to
catch. Probing asks the server what it is.

A bundled model-server process managed by the Hive Stand only: simpler, but it rules out the Nuc
(codingrules 8.10), a Real Cell that keeps working when disconnected because its own server runs
on it.

A fixed list of server commands in code: this puts `llama-server` and ports in source, which
`scripts/check_no_model_ids.py` rejects, and an operator could not run a server we had not heard of.

Having the Warden request each model load from the Queen: uniform, but it makes the Queen a
bottleneck for something already inside the Warden's ceilings, which codingrules 8.10 says needs no
ask.

Leaving local cost at zero and usage at `None`: the `llm.call` event requires a usage, a cost view
that silently drops local calls undercounts, and an operator comparing a local slot with a hosted
one would be comparing against nothing.

Overlays keyed by model id at call sites (`if model.startswith(...)` in the code that builds the
prompt): puts model ids in code, scatters one fix across every caller, and means a new model needs a
code change instead of a manifest line.

Overlays keyed by provider kind: two OpenAI-compatible servers with different models would share
one overlay, and the kind cannot tell a 3B from a 7B behind the same adapter.
