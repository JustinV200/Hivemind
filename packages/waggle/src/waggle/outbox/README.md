# waggle.outbox

The outbox is where a node keeps what it could not send. While a link is down, every frame a bee
loop fails to send waits on disk, and once the link is back the same loop drains it in order. The
decision is ADR-0005, and `docs/waggle/spec.md` section 10 is the wire side.

```text
  send fails ──► Outbox.append (unsigned) ──► JSONL log on disk
                                                   │
  reconnect ──► expire_older_than ──► replay_outbox ──► sign at send time ──► transport
                                          │                                     │
                                          └── ack each one sent ◄───────────────┘
                                              stop at the first transport error
```

Frames are stored unsigned and signed when they are finally sent, so a key rotated between a crash
and the replay still signs correctly, and a stolen outbox file holds nothing a peer would accept.

## Public API

- `Outbox(path)` (`queue.py`): the first-in-first-out queue, with `append`, `ack` and `pending`.
- `replay_outbox(outbox, transport)` and `ReplayReport` (`replay.py`): send what is pending in
  order, ack each frame after it is sent, and leave the tail pending at the first transport error.
- `expire_older_than` (`replay.py`): drop what waited too long, before the replay.

`log.py`, the append-only JSONL file underneath with its torn-line rule and compaction, is the
queue's own business and is not exported.

## How to test this

```bash
uv run --frozen pytest packages/waggle/tests/outbox
```

`test_log.py` covers torn lines and compaction, `test_queue.py` the queue's order and acks across a
reopen, and `test_replay.py` a replay that stops at a failing transport and picks up where it left
off.
