# hivemind.workers.tools

The tools package holds the tool implementations a Worker can call while it works, each one going
through its Cell's `CellSession` rather than touching a process or file directly. Roadmap step 3.16
gives the Drone its first five: `run_command`, `read_file`, `write_file`, `http_request` and `ask`.

## Modules

- `registry.py` -- `ToolSpec` (a tool's schema plus how to run it), `ToolInvocation` (the
  WorkerContext plus the current TaskAssign every tool runner receives), `ToolRunner` (the
  Protocol a runner implements), `ToolRegistry` (validates a call against its schema, then runs
  it; an unknown tool or a schema violation returns readable text, never raises; a
  `hivemind.workers.tools.errors.ToolError` becomes its message; a control exception such as
  `HandoffRequestedError` or `WorkerCancelledError` propagates unchanged) and `build_registry` (offers
  `run_command`/`read_file`/`write_file`/`ask` always, `http_request` only when the Worker holds a
  `net` capability).
- `session.py` -- `run_command` (a COMMAND proposal, `SCRATCH_WRITE` or `OUTSIDE_SCRATCH_WRITE`
  depending on the resolved working directory), `read_file` (no proposal; requires an `fs:read`
  capability outside scratch; truncates to `MAX_TOOL_RESULT_CHARS`) and `write_file` (a whole-file
  DIFF proposal with one `FILE_EXISTS` postcondition, at the same two tiers as `run_command`).
  Every side-effecting call goes through `hivemind.workers.tools.proposals.cap` first, inside
  scratch included: nothing lands uncapped, and the scratch-tier check ladder is cheap.
- `http.py` -- `http_request`: checks a `net` capability for the URL's host, then proposes an
  `ACTION_SEQUENCE` at `RiskTier.NETWORK_EGRESS`. `waggle.messages.capping.ActionKind` has no
  network shape yet, so v0's `SchemaCheck` always rejects it; the tool reports that rejection and
  the request is never actually sent. `httpx` is imported only inside the one branch a `VERIFIED`
  outcome would reach (unreachable in v0), and nowhere else under `workers/`. A real network
  `ActionKind` is a later waggle minor bump.
- `ask.py` -- `ask`: raises a blocking `waggle.messages.supervision.Question` through
  `ctx.asker.ask` and returns its `Answer`'s text (plus the chosen option's own wording, when one
  was offered) as the tool result. No proposal: asking has no side effect to check.
- `proposals.py` -- `ProposalRequest` (a tool's tier, action, postconditions and reason, bundled
  so `make_proposal` stays under codingrules 5.1's parameter limit), `make_proposal` (build a
  Proposal from one), `cap` (propose, then run, through `ctx.capping`) and `describe` (render a
  `GateOutcome` as tool-result text: state, reason, every check and postcondition -- never the
  diff or command text itself).
- `errors.py` -- `ToolError` (root) and `UnreachablePathError` (a path this Worker's session
  cannot reach at all, distinct from merely lacking a capability for it).

## Public API (roadmap 3.16)

See the `Public API:` section of `__init__.py` for the full, current list; the summary above names
each name's home module.

## How to test this

```
uv run --frozen pytest packages/hivemind/tests/unit/workers/tools
```

`tests/unit/workers/tools/` mirrors this package module for module. `tests/builders/workers.py`'s
`make_context` (`hivemind.workers.context.WorkerContext`) now builds a real
`hivemind.supervision.capping.CappingGate` over a `FakeSession`, `NoopSnapshotter`,
`MemoryPheromoneTrail` and `docs/supervision/capping-tiers.toml`, plus a `FakeLeaseView` and a
`DirectCallGate`, so a tool test exercises the real gate rather than a stub. `builders.llm.
make_tool_call` builds the `ToolCall` a `ToolRegistry.execute` call needs.
