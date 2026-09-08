# waggle

The waggle package is the Hive's shared wire protocol and shared primitives: message envelopes,
ids, the clock and the standard long-running loop shape. It is deliberately dependency-light
(pydantic, websockets, cryptography only) and never imports anything from `hivemind`, because the
Pollen Packet (the lightweight agent that runs on a borrowed device, in `packages/pollen`) links
against it directly and must install on hardware with no room for the full Queen stack.

## Public API (roadmap step 0.5)

- **Ids** (`waggle.ids`, encoding in `waggle.ulid`, typed constructors in `waggle.minting`):
  `IdKind` and thirteen `NewType` id types (`HiveId`, `CellId`, `LeaseId`, `TaskId`, `WorkerId`,
  `WardenId`, `AlarmId`, `GrantId`, `ToolId`, `NodeId`, `EventId`, `DeviceId`, `MessageId`), each
  generated as a prefixed ULID (`cell_01H...`) so an id is self-describing in a log line and sorts
  lexicographically by creation time. `new_id` (`waggle.ids`) and the thirteen `new_<kind>_id`
  wrappers (`waggle.minting`, split out of `ids.py` so each file stays under the size limit) mint
  one; `parse_id` validates a candidate string; `timestamp_of` reads its creation time back out.
- **Clock** (`waggle.clock`): the `Clock` protocol every time-reading component depends on,
  `SystemClock` (the real clock) and `FakeClock` (a clock a test drives by hand with `advance()`).
- **TickLoop** (`waggle.loop`): the standard long-running loop shape from codingrules section 11,
  with capped exponential backoff on recoverable errors, that the Queen, every Warden, every
  Worker and the Pollen gateway all subclass.
- **Errors** (`waggle.errors`): `WaggleError`, the package's own root (it cannot inherit from
  `hivemind.common.errors.HiveMindError`, since waggle may not import hivemind), and
  `InvalidIdError`, raised by `parse_id`/`timestamp_of` on a malformed id; plus the protocol
  error tree (`CodecError`, `SignatureError`, `TransportError`, `OutboxError` and their
  subclasses), each with a stable `code` string, that phase 1's codec, signing, transports and
  outbox raise.

Message envelopes, the codec, signing, transports and the outbox (the rest of the tree in
`.claude/codingrules.md` section 3) are not implemented yet; they land in phase 1.

## How to test this

```bash
uv run --frozen pytest packages/waggle/tests
```

Coverage floor is 95% (codingrules section 14.1):

```bash
COVERAGE_FILE=.coverage.waggle uv run --frozen pytest -p no:cacheprovider --cov=waggle \
    --cov-report=term-missing packages/waggle/tests
```

`FakeClock` (in `waggle.clock`, not under `tests/`) is what makes `TickLoop`'s backoff tests run
instantly and deterministically: a test drives time forward with `advance(seconds)` instead of
waiting on a real timer.
