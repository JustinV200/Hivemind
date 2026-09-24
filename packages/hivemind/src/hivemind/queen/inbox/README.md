# hivemind.queen.inbox

The inbox package is the Queen's inbox: where every attached Warden's questions, Alarms and task
events queue up for Attendant (the inbox triage every supervisor uses) before the Queen acts.
Unlike `hivemind.wardens.inbox`, this package MAY import `hivemind.llm`: only anything under an
`autopilot/` directory may not.

## Public API (roadmap step 3.20)

- `LinkReaders`, `LINK_QUEUE_SIZE` (`links.py`): one reader task per attached Warden link, started
  by `hivemind.queen.attach.attach_warden` and reaped on detach and on `Queen.stop`, each feeding
  its own bounded queue (at most `LINK_QUEUE_SIZE` envelopes; a full queue parks the reader, so the
  rest wait with the transport). The Queen's tick waits until anything is queued (or her stop flag
  or wake signal), then `drain`s everything queued on every link -- never more than one queue's
  worth from a single link, so a flooding Warden cannot starve the rest -- before her Attendant
  orders it. `heard()` is the newest Heartbeat each link delivered, handled or not: the liveness
  sweep judges by it, so a stall of the Queen's own tick (a Virtual Cell provision awaited inline,
  a long awake episode) is never mistaken for a silent Warden.
- `queen_attendant`, `to_inbox_item` (`weights.py`): build the Queen's own
  `hivemind.supervision.attendant.Attendant` over `WeightTable.queen_default()`, and classify one
  received envelope into the `InboxItem` shape it scores. A `CellWaxProposed` (roadmap step 4.2a)
  classifies as `WAGGLE_MESSAGE`, the same low base weight as any other routine bee traffic --
  deliberately not its own `InboxKind`.
- `ModelTieBreaker` (`tie_breaker.py`): the model-backed arbiter, on `ModelSlot.ATTENDANT`, for
  an exact score tie -- the seam `hivemind.supervision.attendant.Attendant.order` calls only when
  deterministic scoring cannot separate two or more items.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/queen/inbox -q
```
