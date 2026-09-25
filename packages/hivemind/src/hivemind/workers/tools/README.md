# hivemind.workers.tools

The tools package holds the tool implementations a Worker can call while it works, each one going
through its Cell's `CellSession` rather than touching a process or file directly. Roadmap step 3.16
gives the Drone its first five: `run_command`, `read_file`, `write_file`, `http_request` and `ask`;
roadmap step 5.0e adds a sixth, `keep`; roadmap step 6.5 adds the Exoskeleton's tools (the
`exoskeleton/` sub-package), offered only when a task has a display, a browser or audio attached;
roadmap step 7.8 adds `recall` and `remember`, the Worker's own way into the Honey Store (the Hive's
knowledge base). The Forager (roadmap step 6.9) reuses this same `build_registry(ctx)` unchanged,
so it gets every one of these tools too. The Scout (6.10) does not: none but `read_file` belongs on
a strictly read-only role, so its own, much narrower registry lives beside that role instead
(`hivemind.workers.roles.scout.tools.scout_build_registry`), built the same way but offering
`read_file`, a GET-only `http_request` and its own `report_findings`, plus the Exoskeleton's reads
(never an action) when a browser or display is attached.

## Modules

- `registry.py` -- `ToolSpec` (a tool's schema plus how to run it), `ToolInvocation` (the
  WorkerContext plus the current TaskAssign every tool runner receives), `ToolRunner` (the
  Protocol a runner implements: it returns text, or a `ToolOutput`), `ToolOutput` (roadmap step
  6.5: the text, any media beside it -- a screenshot as `hivemind.llm.ImagePart`, a recording as
  `hivemind.llm.AudioPart` -- and an explicit `is_error` for a failure the text alone does not
  show), `ToolRegistry` (validates a call against its schema, then runs it and always returns a
  `ToolOutput`; an unknown tool or a schema violation returns readable text, never raises; a
  `hivemind.workers.tools.errors.ToolError` becomes its message; a control exception such as
  `HandoffRequestedError` or `WorkerCancelledError` propagates unchanged) and `build_registry`
  (offers `run_command`/`read_file`/`write_file`/`ask`/`keep` always, `http_request` only when the
  Worker holds a `net` capability, each Exoskeleton tool only when its peripheral is attached and
  the bound model can take what it returns, and `recall`/`remember` only when `ctx.honey` is set
  and the Worker holds `tool:recall`/`tool:remember`). The Drone's executor passes a
  `ToolOutput`'s media through as `hivemind.llm.ToolResultPart.media` and records the text alone.
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
- `keep.py` (roadmap step 5.0e) -- `keep(source, destination)`: moves a scratch file to a path
  outside it, an ordinary `outside_scratch_write` through the same gate, leave policy and Leavings
  ledger every other outside-scratch write goes through. `source` must resolve inside scratch
  (refused otherwise, including a symlink that resolves outside it); `destination` must expand
  (`~`) to an absolute path outside scratch. Proposes one `ActionKind.COPY` action -- the source's
  sha256 and size, never its bytes -- with one `FILE_EXISTS` postcondition at the destination; on
  a verified apply the source is removed from scratch (a move, not a copy). `describe()`'s own
  leave-decision line tells the model plainly whether the destination will actually remain.
- `honey.py` (roadmap step 7.8) -- `recall(query, scope?)` asks the Honey Store a `HoneyQuery` as
  this Worker, for its own task, capped at its assignment's clearance and at
  `RECALL_BUDGET_FRACTION` of its model's window (at most `RECALL_MAX_TOKENS`), through
  `ctx.honey`; the hits come back as one delimited `<<<retrieved>>>` block opening with
  `hivemind.memory.RETRIEVED_PREAMBLE`, each hit rendered by `hivemind.memory.render_hit` -- the
  exact shape `memory.assemble` gives a prompt's RETRIEVED section, so retrieved text reads as
  reference data, never instructions, and can never close its block early. A hit labelled above
  the assignment's clearance is dropped here too. `remember(title, text)` deposits a markdown
  FINDING at the assignment's clearance (`hivemind.workers.nectar.split_deposit` cuts it into
  Waggle chunks) and answers with a one-line confirmation. Neither has a side effect on the Cell,
  so neither goes through Capping, like `ask`.
- `proposals.py` -- `ProposalRequest` (a tool's tier, action, postconditions and reason, bundled
  so `make_proposal` stays under codingrules 5.1's parameter limit), `make_proposal` (build a
  Proposal from one), `cap` (propose, then run, through `ctx.capping`; on a `ROLLED_BACK` outcome
  notes `ROLLBACK_ALARM_KIND` (`POSTCONDITION_FAILED`) on `ctx.telemetry` -- at once for a GUI
  action (ADR-0032: every later step would act on a misread screen), and toward the count of
  three for any other -- so the runtime raises a real Alarm instead of the rollback only ever
  showing up as tool-result text), `describe` (render a `GateOutcome` as tool-result text: state,
  reason, every check and postcondition, and -- roadmap step 5.0e, only when the proposal touched
  a path outside scratch -- each such path's own leave verdict and whether it will actually
  remain; never the diff, command or typed text itself) and `tool_output` (roadmap step 6.5:
  `describe`'s text as a `ToolOutput`, an error unless it VERIFIED; a judge's REJECT of an applied
  irreversible GUI action leads the text with the verdict and its reasons and is an error too,
  with no second Alarm, since the gate raised one when the judge ruled; APPROVE and
  REQUEST_CHANGES add one line of verdict).
- `errors.py` -- `ToolError` (root) and `UnreachablePathError` (a path this Worker's session
  cannot reach at all, distinct from merely lacking a capability for it).
- `exoskeleton/` (roadmap step 6.5) -- the Worker's hands, eyes and ears on the Exoskeleton: the
  desktop input tools (`click`, `move`, `type`, `press`, `scroll`), the browser tools
  (`browser_navigate`, `browser_click`, `browser_fill`, `browser_press`), the read-only sight
  tools (`see`, `browser_screenshot`, `browser_snapshot`, `browser_read`) and the audio tools
  (`listen`, `say`). Every action is one typed GUI proposal at the tier its reach implies, with
  the call's optional `expect` as its postcondition; reads propose nothing. See that package's
  own README for the tool-by-tool table.

## Public API (roadmap 3.16)

See the `Public API:` section of `__init__.py` for the full, current list; the summary above names
each name's home module.

## How to test this

```
uv run --frozen pytest packages/hivemind/tests/unit/workers/tools
```

`builders.workers.make_gui_context` builds the same context with an Exoskeleton attached: the
fake peripherals a test passes, and a real gate whose GUI surface is a real
`hivemind.exoskeleton.surface.ExoskeletonSurface` over them; `run_tool` runs one call as the Drone
would, and `proposals_of` reads back what the gate was asked to cap.

`tests/unit/workers/tools/` mirrors this package module for module. `tests/builders/workers.py`'s
`make_context` (`hivemind.workers.context.WorkerContext`) now builds a real
`hivemind.supervision.capping.CappingGate` over a `FakeSession`, `NoopSnapshotter`,
`MemoryPheromoneTrail` and `docs/supervision/capping-tiers.toml`, plus a `FakeLeaseView` and a
`DirectCallGate`, so a tool test exercises the real gate rather than a stub. `builders.llm.
make_tool_call` builds the `ToolCall` a `ToolRegistry.execute` call needs.
`builders.honey_wire.FakeHoneyChannel` stands in for `ctx.honey` the way `builders.workers.
FakeAsker` stands in for `ctx.asker`.
