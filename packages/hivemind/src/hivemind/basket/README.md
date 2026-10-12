# hivemind.basket

The Basket holds the bytes work produces and Honey cannot represent: a compiled binary, a wheel
set, a dataset, a Comb tool's package, the weights a Nuc will serve. Named for the pollen basket on
a bee's leg. Every blob is content-addressed (its sha256 is its identity), signed by the Hive key
at deposit, labelled with a `HoneyClearance` from its provenance, and described by exactly one Honey
record, which is the only way a bee finds it (retrieval returns the few records that match a task;
`memory.assemble` never lists the Basket). The canonical copy lives on the Hive Stand; each Cell
keeps the blobs its Warden chose, within the resident-basket ceiling the Queen set; a blob reaches
another Cell over Waggle through the Hive Stand, verified on arrival (ADR-0048).

**A blob is inert.** Holding one grants nothing. The store has no method that runs, installs or
loads a blob, and the package imports nothing that could; running a script from a blob is an
ordinary capped command, and only a Comb promotion makes a blob executable, by naming its hash as a
tool package. `tests/unit/basket/test_inert.py` asserts it.

Layer 2, beside `honey_store`. It imports `common`, `pheromone`, `cell`, `guard` (the scanner) and
waggle; never `honey_store` (a deposit's Honey record goes through `HoneyRecordPort`), `memory`,
`llm`, `supervision`, `brood_chamber` or anything above.

## Public API

- **Models** (`models.py`): `Blob` (sha256, size, `MediaKind`, MIME type, platform tags, clearance,
  origin Cell/task/bee, `deposited_at`, `deposit_event_id`, `tainted`, `BlobState`),
  `DepositRequest`, `HoneyRecord`, and `HoneyRecordPort`, the one-method port the composition root
  plugs a Nectar adapter into (`source_key` `basket:<sha256>`).
- **Manifest** (`manifest.py`): `BlobManifest` = a `Blob` plus the signer's node id and an Ed25519
  signature over its canonical JSON (sorted keys, a purpose string for domain separation, and not
  `state`, so a retire never breaks a shipped copy). `BlobManifest.sign(blob, signer_id, signer)`,
  `.verify(verifier)`.
- **Clearance** (`clearance.py`, pure): `blob_floor` (C2 from a borrowed Cell, else C0),
  `blob_clearance` (declared raised to the floor), `receiver_ceiling` (Night Veil takes at most
  C1), `receipt_refusal` / `may_receive` (retired, tainted, clearance, platform). Restates the Honey
  Store's rules; `tests/unit/basket/test_clearance.py` cites its test as the source of truth.
- **Deposit door** (`deposits.py`): `BasketDeposits.deposit(request) -> DepositOutcome(blob, is_new,
  events)`. Order: size cap, label (a Night Veil Cell may not export above C1), scanner (text-like
  content only), then the Honey record (new blobs only), then the store. A repeat deposit of the
  same bytes is deduplicated and may only raise the stored label. The door holds no trail: events
  are returned, or attached to the error it raises.
- **Store** (`store/`): `BasketStore` protocol; `SqliteBasketStore(connection, root, clock)` keeps
  metadata in the Hive database (`basket_blobs`, `basket_residency`, `basket_shipments`) and bytes
  in `<root>/<sha256[:2]>/<sha256>`, written to `.<sha>.part` then renamed, and re-hashed on every
  read. `retire` keeps the row and bytes and makes `open_bytes` refuse. `apply_basket_migrations`.
- **Residency** (`residency.py`): `decide_residency(resident, incoming, ceiling)` (pure, oldest
  `last_used` first, refuses a blob bigger than the ceiling), `ResidentBasket` (per-Cell facade:
  `plan`, `admit`, `is_resident`, `bytes_for` which re-hashes, `evict`).
- **Transfer** (`transfer/`): `Offer`, `Chunk`, `Ack`, `Refuse`, `RefusalCode`, `CHUNK_BYTES`
  (256 KiB, so a base64 chunk fits `MAX_FRAME_BYTES`), `TransferSender` and `TransferReceiver`,
  pure state machines over those values. `transfer/wire.py` (a later step) maps them to the
  `basket.*` Waggle messages.
- **Events** (`events.py`): `basket_event`, `blob_facts`, `EventStamp`. Payloads carry ids, hashes,
  sizes and labels, never bytes, titles or summaries.
- **Errors** (`errors.py`): `BasketError` and `BlobNotFoundError`, `BlobTooLargeError`,
  `BlobRefusedError`, `BlobIntegrityError`, `ShipmentNotFoundError`, `TransferError`.

## The deposit, in order

```text
DepositRequest ──► bytes <= max_blob_bytes? ──► label = max(declared, floor) ──► Night Veil <= C1?
                        │ no: BlobTooLargeError          │                          │ no: refused
                        ▼                                ▼                          ▼
              basket.deposit_refused          text-like? ──► Guard scanner ──► flagged: refused
                                                                  │ clean
                                              known hash? ── yes ─► dedupe (label may rise), no Honey record
                                                   │ no
                                       sign manifest ─► HoneyRecordPort.record ─► store.deposit
                                                                  ▼
                                                        basket.deposited (returned, not recorded)
```

## A transfer

```text
sender                                          receiver
Offer(manifest) ─────────────────────────────►  verify signature; refuse by rule before any byte:
                                                CLEARANCE, TAINTED, PLATFORM, NO_ROOM,
                                                UNKNOWN_SIGNER, INTEGRITY
          ◄──────────────────── Ack(received_bytes)  (resumes from a partial file)
Chunk(offset, data, is_last) ────────────────►  in order only; a repeat is re-acked, not appended
          ◄──────────────────── Ack(received_bytes)  ... until is_last
                                                hash the whole spool file: match -> Ack(complete),
                                                mismatch -> delete it, Refuse(INTEGRITY) and an
                                                `integrity_failures` entry for the caller to trail
```

## How to test this

```bash
uv run pytest -q packages/hivemind/tests/unit/basket \
  packages/hivemind/tests/contracts/test_basket_store_contract.py
```

The contract suite runs over `:memory:` and a temp-file database; the residency properties use
hypothesis; `test_inert.py` reads the package's syntax tree for forbidden imports and calls.
Builders are in `tests/builders/basket.py` (`make_blob`, `make_deposit_request`, `make_manifest`,
`make_kit`, `FakeHoneyPort`).
