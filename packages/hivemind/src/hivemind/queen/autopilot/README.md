# hivemind.queen.autopilot

The autopilot package is the Queen's Autopilot: deterministic fallback behaviour that never
awaits a model. Nothing under this package may import hivemind.llm.

## Public API (roadmap steps 3.20, 4.7)

- `QueenAction` (`actions.py`): the closed set of moves the dispatch table can pick -- `RECORD`,
  `DISPATCH`, `COMPLETE_TASK`, `RETRY_TASK`, `FAIL_TASK`, `REBIND`, `ESCALATE_TO_HUMAN`,
  `BLOCK_ON_QUESTION`, `ROUTE_ANSWER`, `MARK_WARDEN_OFFLINE`, `NEEDS_JUDGEMENT`.
- `decide` (`table.py`): the pure dispatch table itself, keyed by the wrapped payload's own type
  (and the task's own status, where it matters); an escalated Alarm is mapped through
  `hivemind.supervision.policy.decide`, capped by the Queen's own attempt ceiling.
- `effort_for` (`effort.py`): the `Effort` an awake episode gets, by `InboxKind` -- alarms `HIGH`,
  questions `MEDIUM`, everything else `LOW`; a table, not a chain of branches.
- `ForageAutopilotOutcome`, `ForageRequestSignal`, `decide_forage_request` (`forage.py`, roadmap
  step 4.7): the deterministic rule for one `ForageRequest` -- `GRANT` when
  `hivemind.queen.forage.ledger.ForageLedger.headroom` alone covers it, `NEEDS_JUDGEMENT` when it
  does not but shrinking another live grant could, `DENY` otherwise. `hivemind.queen.ticks.forage`
  is this rule's one caller, reached ahead of `decide` for exactly the same reason `decide`'s own
  `NEEDS_JUDGEMENT` fallback would otherwise catch every ForageRequest.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/queen/autopilot -q
```
