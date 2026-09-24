# hivemind.honey_store.nectar

The intake side of the Honey Store (the Hive's knowledge base). Nectar is raw information a bee
(a Worker or a Warden) or the Hive itself brings back: a finding, a transcript, a tool result, a
Handoff. Every deposit enters through `NectarIntake`, which caps it, labels it with a
`HoneyClearance`, files it under a scope, applies the Night Veil rule and stores it, deduplicated,
with its Pheromone Trail events, in one transaction (ADR-0031). The House Bee's ripening pipeline
(`hivemind.honey_store.ripening`) turns what is stored here into Honey.

## Public API (roadmap 7.4)

- **`NectarIntake`** (`intake.py`):
  - `submit(submission)` takes a whole deposit in-process (a verified task's outcome, aged Bee
    Bread, cleared Cell Wax, the human's proposed note).
  - `receive_chunk(deposit, source)` takes one Waggle `NectarDeposit` chunk and returns the result
    once the final chunk verifies (None before that).
  - `expire_groups(now)` drops incomplete deposits idle past the spec's 60 seconds; the Queen's
    tick calls it.
- **`NectarSubmission`, `DepositSource`, `IntakeResult`** (`submission.py`): what intake takes and
  returns. `submission_from_deposit` turns a verified chunked deposit into a submission;
  `handoff_source_key(event_id)` builds `handoff:<event id>`, the dedupe key a Handoff carries
  whether it arrives over Waggle or from Bee Bread, so both arrivals are one row.
- **`ChunkGroups`** (`reassembly.py`): reassembly under docs/waggle/spec.md section 5, with the
  spec's `MAX_OPEN_CHUNK_GROUPS` (8) and `CHUNK_GROUP_TIMEOUT_S` (60) re-exported from
  `waggle.messages.base`.

## The rules, in the order intake applies them

```text
chunk ──► cell matches the Warden's own Cell? ──► ChunkGroups.accept ──► whole content (verified)
                                                                            │
submission (in-process) ────────────────────────────────────────────────────┤
                                                                            ▼
      size <= [honey.store] max_nectar_bytes ──► intake_label ──► scope_for_nectar
                                                                            │
      Night Veil tier?  RIPENED_HONEY at C0/C1 ─► ordinary row, origin_tier NIGHT_VEIL, recorded
                        RIPENED_HONEY at C2    ─► NightVeilRefusedError, no event
                        anything else          ─► EPHEMERAL row (purged at teardown), no event
      otherwise         ─► HoneyStore.add_nectar with nectar_received / nectar_deduplicated
                           (+ label_raised when a duplicate raised the stored label)
```

Every refusal raises a `NectarRejectedError` subclass with a stable `code` (the Queen answers the
sender with `control.error`) and, except for a Night Veil source, records `honey.nectar_rejected`
with the code, the sender, a 12-character digest prefix and the size; never content.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/honey_store/nectar
```

`test_reassembly.py` property-tests chunk ordering and the digest check with hypothesis;
`test_intake.py` runs against a real SQLite store from `builders.honey` and reads the trail back.
