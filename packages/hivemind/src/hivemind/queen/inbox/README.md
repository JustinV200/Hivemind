# hivemind.queen.inbox

The inbox package is the Queen's inbox: where every attached Warden's questions, Alarms and task
events queue up for Attendant (the inbox triage every supervisor uses) before the Queen acts.
Unlike `hivemind.wardens.inbox`, this package MAY import `hivemind.llm`: only anything under an
`autopilot/` directory may not.

## Public API (roadmap step 3.20)

- `queen_attendant`, `to_inbox_item` (`weights.py`): build the Queen's own
  `hivemind.supervision.attendant.Attendant` over `WeightTable.queen_default()`, and classify one
  received envelope into the `InboxItem` shape it scores.
- `ModelTieBreaker` (`tie_breaker.py`): the model-backed arbiter, on `ModelSlot.ATTENDANT`, for
  an exact score tie -- the seam `hivemind.supervision.attendant.Attendant.order` calls only when
  deterministic scoring cannot separate two or more items.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/queen/inbox -q
```
