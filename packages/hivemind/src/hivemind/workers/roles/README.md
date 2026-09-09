# hivemind.workers.roles

The roles package holds one module (or package) per Worker role: Forager, Scout, GuardBee,
Undertaker, Drone and HouseBee, each implementing the shared Worker protocol.

## Modules

- `drone/` -- `Drone` (roadmap step 3.16): a generic, disposable role that assembles its own
  hot-state prompt and runs a bounded `hivemind.llm.run_tool_loop` over the tools its capabilities
  allow, until the model stops calling tools, the round cap is reached, or its own telemetry says
  to checkpoint. Split by responsibility: `sources.py` (`DroneSources`, what one attempt knows for
  hot-state packing), `prompt.py` (assembling that hot state and building the `LLMRequest`),
  `outcome.py` (`HandoffRequestedError`, the tool-executor adapter that cooperates with pause, cancel
  and handoff, and the two ways one attempt ends); `Drone` itself lives in the package's own
  `__init__.py`. See `hivemind.workers.roles.drone`'s own docstring for the full shape.

## Public API (roadmap 3.16)

See the `Public API:` section of `__init__.py` for the full, current list.

## How to test this

```
uv run --frozen pytest packages/hivemind/tests/unit/workers/roles
```

`tests/unit/workers/roles/` mirrors this package module for module. `tests/builders/workers.py`'s
`make_context` now builds a real `hivemind.supervision.capping.CappingGate`, so a Drone test's
tool calls exercise the real gate, not a stub; `builders.llm.FakeLLMProvider` (via `make_bound`)
scripts what the model says.
