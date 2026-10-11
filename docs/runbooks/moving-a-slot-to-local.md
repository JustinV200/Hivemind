# Moving a slot to a local model

Roadmap step 8.9. This is how an operator moves one model slot (`worker`, `ripener`, `judge`,
`embedder` or `transcriber`; the others work the same way) from a hosted provider to a local
server, using only manifest edits. No code changes, and no restart of anything but the Hive. The
reasoning is in ADR-0044 (local serving) and ADR-0045 (routing and offline mode).

`hive llm eval --update-map` (step 3 below) is phase 8's evaluation harness. Until step 8.5
lands, skip that step: the hand-set grade stands.

## Before you start

- A local model server is running and answers the OpenAI-compatible routes on a loopback address
  (Ollama, vLLM, llama.cpp's server and LM Studio all do). Note its base URL, such as
  `http://127.0.0.1:<port>/v1`, and the model id it serves.
- For an in-process embedder or transcriber, install the extra (`hivemind[embeddings]` or
  `hivemind[whisper]`) and fetch the model once while you are still online. Offline mode never
  downloads one.
- Know the model's honest limits: context window, whether it takes native tool calls, JSON schema
  output or images. Look them up, or run `hive llm test` and read what it does.
- Keep a copy of your manifest. Every step below edits only that file.

## 1. Add the provider

In `[llm.providers]`, add the server. Skip this if the server is already there.

```toml
[llm.providers.local]
kind = "openai_compat"
base_url = "http://127.0.0.1:<port>/v1"
seats = 2
```

`seats` is how many concurrent requests the server really allows. Add
`[llm.providers.local.capabilities]` with what the model cannot do (`native_tool_calls = false`,
`schema_output = false`, `vision = false`). A weaker model then takes the prompted rungs of the
degradation ladders instead of failing on a feature it lacks. `docs/manifests/offline.toml` has a
complete example.

For `embedder`, add a provider of `kind = "sentence_transformers"` (in process, no `base_url`) or
point an `openai_compat` provider at an embedding model. For `transcriber`, use
`kind = "whisper_local"` (in process, no `base_url`) or an `openai_compat` provider whose server
has `/audio/transcriptions`. An in-process kind takes `seats = 1`.

## 2. Add the Forage map entry

Every model needs a `[forage.map.<source_id>]` entry, or routing and the allocator cannot see it.

```toml
[forage.map.local_small]
provider = "local"
model = "<model id>"
grade = 2
context_window = 8192
capabilities = ["json_mode"]
seats = 2
vram_bytes_required = 2684354560
```

- `grade` runs from 1 (weakest) to 5 (strongest). Be honest and a little low: a grade above what
  the model earns sends it work it will fail. This is a guess, replaced in step 7.
- `vram_bytes_required` is what the model needs to load, in bytes. The Queen checks it against a
  Cell's free VRAM before she plans to host the model there.
- Leave `cost` out. A self-hosted model is free per token. To count the hardware, set
  `cost.cost_per_seat_hour_usd` (ADR-0044).

## 3. Bind the slot, keeping the hosted binding as the fallback

In `[llm.slots]`, add the hosted binding under a new name and point the slot at the local model,
with the hosted one as its fallback:

```toml
[llm.slots.hosted_worker]
provider = "anthropic"
model = "<hosted model id>"

[llm.slots.worker]
provider = "local"
model = "<model id>"
fallback = "hosted_worker"
```

Routing only reorders a slot's own fallback chain (ADR-0045), so a slot with no hosted fallback
has nowhere to go when the local server is down or the model is too weak for a task's tempo. The
embedder is the exception: a fallback is followed only if it serves the same model id, because
vectors from different models cannot be compared (ADR-0036). Changing the embedder re-embeds the
Honey Store in the background; `hive honey reembed` does it now.

Do not bind the Judge and the Ripener to the same weak model. Phase 7's real runs showed a 3B
Ripener working while a 3B Judge rejected every lowering, a harmless build log included; a 7B Judge
decided every probe correctly (`.claude/phase-7-handoff.md`, section 8, item 2). If you move one of
those slots to a small model, keep the other on a larger one. Run the judge's local eval
(`tests/evals/honey/test_clearance_judge_local.py`, gated by `HIVEMIND_LIVE_LLM=1` and
`HIVEMIND_LOCAL_LLM_BASE_URL`) on any candidate model before you bind it.

## 4. Set offline when everything is local

When no slot's chain contains a hosted provider any more, add `offline = true` under `[llm]` and
remove the hosted providers and bindings. The manifest then refuses to load if any provider is
hosted or not on loopback; an in-process kind is accepted because it opens no connection. Do not
set `offline` while a hosted fallback remains: that is the mistake it exists to catch. The offline
example is `docs/manifests/offline.toml`.

## 5. Check it

```
hive llm providers
hive llm slots
hive llm test <slot>
```

- `hive llm providers` shows each provider's declared capabilities and a live health probe. A
  failing probe means the server is not reachable at `base_url`.
- `hive llm slots` shows each slot's binding, price and fallback chain. Confirm the moved slot
  now shows the local model first and the hosted one behind it.
- `hive llm test <slot>` resolves the slot and sends one real call. For `embedder` and
  `transcriber` it checks the provider answers its own kind of request.

Then run a small real goal and look at the trail: the moved slot's `llm.call` events should name
the local provider, and an `llm.fallback` or `llm.spill` event means the hosted binding took over,
with the reason.

## 6. Prove nothing else changed

Switching a slot changes only the manifest. A checksum of `src/` is identical before and after,
and phase 8's end-to-end job asserts exactly that between the hosted and local configurations
(the phase 8 exit criteria). You can check yours the same way:

```
find packages -path '*/src/*' -type f -name '*.py' | sort | xargs sha256sum | sha256sum
```

Run it before and after. If the two differ, something other than the manifest was edited.

## 7. Replace the guess with a measurement

```
hive llm eval --update-map
```

The harness runs a fixed goal set and graders against the Hive's slots, writes a report to
`docs/evals/<date>.md`, and with `--update-map` stores a measured grade for each source in the
Queen's ledger, where it overrides the grade you wrote (ADR-0045). The manifest file is not
changed. Routing's grade floors now rest on how the model actually did.

Read the report before you trust it. If the moved slot's measured grade is below its floor
(`[llm.floors]`, defaults: the Queen 5, the Attendant, Ripener, Embedder and Transcriber 1,
everything else 2), routing will skip the local binding and use the hosted fallback; that is the
evidence saying this slot should stay hosted. A transcriber is graded by word error rate and
latency, so use the report to pick the Whisper size.

## How the offline proof runs

`docs/manifests/offline.toml` is proven two ways, and neither needs you to do anything.

On every push, `packages/hivemind/tests/e2e/offline/` derives a manifest from that file, changing
only what a machine with no model server forces (the chat server's address becomes a scripted
OpenAI-compatible server on a free loopback port, the in-process embedder and Whisper become
`openai_compat` rows on it, every model id becomes a scripted one) and keeping `offline = true`,
the slot wiring and the Forage map. It then runs the haiku goal on the Hive Stand (in process and
through `hive run`) and in a Virtual Cell, ripening with RIPENER and EMBEDDER on the server, a goal
spoken as an audio clip, a provider outage and wake, and the browser Forager with and without
vision, each under a guard that replaces `socket.socket.connect`, `connect_ex` and
`socket.create_connection`: a connect to any address off `127.0.0.0/8` and `::1` fails the test with
the address, and each case also asserts every `llm.call` on the trail names a provider that runs on
this machine. One case compares a checksum of `packages/*/src/**/*.py` before and after the goal
runs under the offline manifest and under the fake provider's, which is the check above, run by a
test.

Nightly, and by hand from the Actions tab, the `local_llm` job in
`.github/workflows/integration.yml` runs the same claims against Ollama installed on the runner
(`packages/hivemind/tests/evals/offline/`, plus the other `local_llm` tests), then `hive llm eval`
with `--update-map`, and uploads `docs/evals/`. To run it on your own machine, start your server and
set `HIVEMIND_LIVE_LLM=1`, `HIVEMIND_LOCAL_LLM_BASE_URL`, `HIVEMIND_LOCAL_LARGE_MODEL` and
`HIVEMIND_LOCAL_SMALL_MODEL`; the tests skip cleanly when they are unset. The guard covers sockets
the test process opens; a child process such as the browser's is outside it.

## Moving a slot back

Edit the slot's `provider`, `model` and `fallback` back. Nothing else needs to change. Leave the
local provider and its Forage map entry in place if you may move again.
