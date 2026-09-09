# hivemind.wardens.inbox

The inbox package is the Warden's inbox: where questions, Alarms and results from its Workers
queue up for Attendant (the inbox triage every supervisor uses).

## Public API (roadmap step 3.19)

- `warden_attendant` (`weights.py`): builds this Warden's own `hivemind.supervision.attendant.
  Attendant` over `WeightTable.warden_default()`, with no `TieBreaker` (autopilot-only).
- `to_inbox_item` (`weights.py`): classifies one received `waggle.envelope.Envelope`'s payload
  into the `InboxItem` shape the Attendant scores.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/wardens/inbox -q
```
