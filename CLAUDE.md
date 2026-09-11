# HiveMind

Read these before writing or changing anything:

- `.claude/codingrules.md`: the standard every file is held to. Layout, layering, size limits,
  commenting, protocols, testing, security. Non-negotiable; if code and the rules disagree, the
  code is wrong.
- `.claude/roadmap.md`: the build order. Work one step at a time, in order, and tick the box in
  the PR that lands it. Do not start a phase until the previous phase's exit criteria hold.
- `README.md`: the vision and the vocabulary. Code uses the bee terms literally.
- `.claude/subagents.md`: how work splits between the orchestrator and subagents, and the usage
  budget. Subagent tokens share the session limit: few, large dispatches; no tester or reviewer
  subagents by default; the orchestrator runs the gates, applies small edits and commits itself.

The rules that get broken most often:

- Files stay under 300 lines of code (comments and docstrings do not count) and functions under
  50 lines. Every module starts with the header docstring from codingrules 7.2. Comments answer
  *why*, on every logical block.
- A concept that needs a second file becomes a package with an `__init__` face, never a prefixed
  sibling (`outbox_log.py`). A source directory holds at most ten modules; group into
  sub-packages before that.
- Imports flow down the layer table only. No vendor LLM SDK outside `hivemind/llm/providers/`.
  No `subprocess` outside `cell/`, `hive/backends/`, the dev sandbox, and `pollen/`.
- Anything under an `autopilot/` directory never imports `hivemind.llm`. Awake episodes are
  assembled from state by `memory.assemble`, never accumulated as a conversation.
- Model access goes through a `ModelSlot`. Cell access goes through a `CellSession`. Never branch
  on `cell.kind` or `provider.name`; branch on capabilities.
- Real Cells are borrowed and left exactly as found. Wardens never provision Cells.
- `hivemind.llm` imports `hivemind.forage`, never the reverse: `ModelSlot` and `Tempo` live in
  `forage`. Ids, the clock and the loop shape live in `waggle`, because `pollen` needs them and
  may import nothing from `hivemind`.
- Every client, the Observation Hive included, is a device enrolled and approved on the Hive
  Stand's loopback listener; it logs in with its device key plus the operator's password and enters
  through the Landing Board. Approval routes never exist on the remote listener, and there is no
  public exposure mode.
- Prefer a real task over a synthetic one wherever a phase's exit criteria can be exercised by one:
  alongside unit/contract tests, run an actual end-to-end scenario (a real Cell, a real provider
  call, a real Worker doing real work) through whatever the phase just landed. Bad abstractions are
  cheap to fix when a real task finds them mid-phase, expensive once the platform is "done" and the
  first real workload finds them instead.
