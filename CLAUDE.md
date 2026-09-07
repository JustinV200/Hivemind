# HiveMind

Read these before writing or changing anything:

- `.claude/codingrules.md`: the standard every file is held to. Layout, layering, size limits,
  commenting, protocols, testing, security. Non-negotiable; if code and the rules disagree, the
  code is wrong.
- `.claude/roadmap.md`: the build order. Work one step at a time, in order, and tick the box in
  the PR that lands it. Do not start a phase until the previous phase's exit criteria hold.
- `README.md`: the vision and the vocabulary. Code uses the bee terms literally.

The rules that get broken most often:

- Files stay under 300 lines and functions under 50. Every module starts with the header
  docstring from codingrules 7.2. Comments answer *why*, on every logical block.
- Imports flow down the layer table only. No vendor LLM SDK outside `hivemind/llm/providers/`.
  No `subprocess` outside `cell/`, `hive/backends/`, the dev sandbox, and `pollen/`.
- Anything under an `autopilot/` directory never imports `hivemind.llm`. Awake episodes are
  assembled from state by `memory.assemble`, never accumulated as a conversation.
- Model access goes through a `ModelSlot`. Cell access goes through a `CellSession`. Never branch
  on `cell.kind` or `provider.name`; branch on capabilities.
- Real Cells are borrowed and left exactly as found. Wardens never provision Cells.
