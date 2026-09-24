# hivemind.workers.tools

The tools package holds the tool implementations a Worker can call while it works, each one going
through its Cell's `CellSession` rather than touching a process or file directly. Roadmap step 3.16
gives the Drone its first five: `run_command`, `read_file`, `write_file`, `http_request` and `ask`;
roadmap step 5.0e adds a sixth, `keep`.

## Modules

- `registry.py` -- `ToolSpec` (a tool's schema plus how to run it), `ToolInvocation` (the
  WorkerContext plus the current TaskAssign every tool runner receives), `ToolRunner` (the
  Protocol a runner implements), `ToolRegistry` (validates a call against its schema, then runs
  it; an unknown tool or a schema violation returns readable text, never raises; a
  `hivemind.workers.tools.errors.ToolError` becomes its message; a control exception such as
  `HandoffRequestedError` or `WorkerCancelledError` propagates unchanged) and `build_registry`
  (offers all six tools always: since roadmap step 10.3 a capability decides at invocation, so a
  refusal is visible). `execute` checks `tool:<name>` through the Guard's `Enforcer`
  (`tool_invocation`) before running a tool.
- `authorize.py` (roadmap step 10.3) -- `authorize(invocation, point, needed)`: the one way a tool
  asks the Guard, as the Worker itself, holding its own set, on its own Cell; a refusal is already
  `guard.denied` on the trail. `refusal_text` renders it for the model, behind the fixed
  `GUARD_REFUSAL_PREFIX` the Drone's outcome records read.
- `session.py` -- `run_command` (a COMMAND proposal, `SCRATCH_WRITE` or `OUTSIDE_SCRATCH_WRITE`
  depending on the resolved working directory), `read_file` (no proposal; outside scratch it
  passes the `session_outside_scratch` point for `fs:read:<path>`; truncates to
  `MAX_TOOL_RESULT_CHARS`) and `write_file` (a whole-file
  DIFF proposal with one `FILE_EXISTS` postcondition, at the same two tiers as `run_command`).
  Every side-effecting call goes through `hivemind.workers.tools.proposals.cap` first, inside
  scratch included: nothing lands uncapped, and the scratch-tier check ladder is cheap.
- `http.py` -- `http_request`: checks `net:<host>` for the URL's host through the Guard
  (`tool_invocation`; a refusal is on the trail), then (roadmap step 10.3a) resolves the host
  through `WorkerContext.resolver` and has the Guard's floors judge every address it got back, so
  a loopback, link-local or Hive Stand address is refused as `guard.state_floor.loopback` however
  the name spelled it; a host the Worker does not hold is never looked up. It then proposes a
  one-step `ACTION_SEQUENCE` (`"<METHOD> <url>"`) at `RiskTier.NETWORK_EGRESS`. Since roadmap step
  10.3 the Capping gate passes a well-formed network step on that tier (its ALLOWLIST rung checks
  `net:<host>` again; its apply is the authorisation itself), and once the outcome is `VERIFIED`
  `_send` makes the one request to the checked address (a `PinnedRequest`), the name kept in
  `Host` and in TLS's SNI; a connection failure is a readable result, never an exception. `httpx`
  is imported here (its URL parser is the one the request is sent with), and nowhere else under
  `workers/`.
- The Hive-state floor also stands in front of `run_command` (`exec:<argv[0]>`: never `hive` or
  `hivemind-*`), `write_file` and `keep` (`fs:write:<path>`: never the Hive's database, secrets or
  manifest), through `authorize.floor_refusal_text`, before anything is proposed.
- `ask.py` -- `ask`: raises a blocking `waggle.messages.supervision.Question` through
  `ctx.asker.ask` and returns its `Answer`'s text (plus the chosen option's own wording, when one
  was offered) as the tool result. No proposal: asking has no side effect to check; since roadmap
  step 10.3 it needs `question:human` (the `question_routing` point).
- `keep.py` (roadmap step 5.0e) -- `keep(source, destination)`: moves a scratch file to a path
  outside it, an ordinary `outside_scratch_write` through the same gate, leave policy and Leavings
  ledger every other outside-scratch write goes through. `source` must resolve inside scratch
  (refused otherwise, including a symlink that resolves outside it); `destination` must expand
  (`~`) to an absolute path outside scratch. Proposes one `ActionKind.COPY` action -- the source's
  sha256 and size, never its bytes -- with one `FILE_EXISTS` postcondition at the destination; on
  a verified apply the source is removed from scratch (a move, not a copy). `describe()`'s own
  leave-decision line tells the model plainly whether the destination will actually remain.
- `proposals.py` -- `ProposalRequest` (a tool's tier, action, postconditions and reason, bundled
  so `make_proposal` stays under codingrules 5.1's parameter limit), `make_proposal` (build a
  Proposal from one), `cap` (propose, then run, through `ctx.capping`; on a `ROLLED_BACK` outcome
  also notes `ROLLBACK_ALARM_KIND` (`POSTCONDITION_FAILED`) on `ctx.telemetry`, so the runtime
  raises a real Alarm instead of the rollback only ever showing up as tool-result text; since
  roadmap step 10.3 it takes the whole `ToolInvocation`, and an ALLOWLIST refusal that names a
  missing capability is also recorded as the Guard's `guard.denied`, at `session_outside_scratch`
  for an outside-scratch write and `tool_invocation` otherwise) and
  `describe` (render a `GateOutcome` as tool-result text: state, reason, every check and
  postcondition, and -- roadmap step 5.0e, only when the proposal touched a path outside scratch
  -- each such path's own leave verdict and whether it will actually remain; never the diff or
  command text itself).
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
