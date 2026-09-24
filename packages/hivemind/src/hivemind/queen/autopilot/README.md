# hivemind.queen.autopilot

The autopilot package is the Queen's Autopilot: deterministic fallback behaviour that never
awaits a model. Nothing under this package may import hivemind.llm.

## Public API (roadmap steps 3.20, 4.7, 4.2a)

- `QueenAction` (`actions.py`): the closed set of moves the dispatch table can pick -- `RECORD`,
  `DISPATCH`, `COMPLETE_TASK`, `RETRY_TASK`, `FAIL_TASK`, `REBIND`, `ESCALATE_TO_HUMAN`,
  `BLOCK_ON_QUESTION`, `ROUTE_ANSWER`, `MARK_WARDEN_OFFLINE`, `WRITE_WAX`, `REJECT_WAX`,
  `CLEAR_WAX`, `QUARANTINE_BEE`, `PAUSE_TASK`, `NEEDS_JUDGEMENT`.
- `decide` (`table.py`): the pure dispatch table itself, keyed by the wrapped payload's own type
  (and the task's own status, where it matters); an escalated Alarm is mapped through
  `hivemind.supervision.policy.decide`, capped by the Queen's own attempt ceiling. Roadmap step
  10.6c: a policy row naming `QUARANTINE` maps to `QUARANTINE_BEE` (carried out by
  `hivemind.queen.quarantine.order_quarantine`), and a Warden's `TaskProgress` at stage PAUSED
  about a task not yet terminal maps to `PAUSE_TASK` (`hivemind.queen.quarantine.hold_task`); every
  other `TaskProgress` stays `RECORD`.
- `effort_for` (`effort.py`): the `Effort` an awake episode gets, by `InboxKind` -- alarms `HIGH`,
  questions `MEDIUM`, everything else `LOW`; a table, not a chain of branches.
- `ForageAutopilotOutcome`, `ForageRequestSignal`, `decide_forage_request` (`forage.py`, roadmap
  step 4.7): the deterministic rule for one `ForageRequest` -- `GRANT` when
  `hivemind.queen.forage.ledger.ForageLedger.headroom` alone covers it, `NEEDS_JUDGEMENT` when it
  does not but shrinking another live grant could, `DENY` otherwise. `hivemind.queen.ticks.forage`
  is this rule's one caller, reached ahead of `decide` for exactly the same reason `decide`'s own
  `NEEDS_JUDGEMENT` fallback would otherwise catch every ForageRequest.
- `WaxAutopilotOutcome`, `WaxProposalSignal`, `decide_wax_proposal` (`wax.py`, roadmap step 4.2a):
  the deterministic rule for one `CellWaxProposed` -- `AUTOPILOT_WRITE` only for a Warden's own
  NOTE or CAUTION about its own Cell, strictly within the manifest's per-Cell cap; `NEEDS_JUDGEMENT`
  for every BLOCK, every other proposer, or one at or over the cap. Reads the wire
  `waggle.messages.cell.wax.WaxSeverity` directly, not the mirrored domain one in
  `hivemind.memory.cell_wax`, because that package's own `__init__` transitively reaches
  `hivemind.llm` (through `hivemind.memory.hot_state.packing`'s `SectionLabel`) and this directory
  may never import it. `hivemind.queen.ticks.wax` is this rule's one caller, reached ahead of
  `decide` the same way `forage.py`'s own rule is.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/queen/autopilot -q
```
