# waggle

The waggle package is the Hive's shared wire protocol and shared primitives: the envelope every
message travels in, the codec that puts it on the wire, Ed25519 signing, the message catalogue,
two transports, the offline outbox, and underneath them ids, the clock and the standard
long-running loop shape. It is deliberately dependency-light (pydantic, websockets, cryptography
only) and never imports anything from `hivemind`, because the Pollen Packet (the lightweight
agent that runs on a borrowed device, in `packages/pollen`) links against it directly and must
install on hardware with no room for the full Queen stack. `docs/waggle/spec.md` is the source of
truth for everything on the wire; `tests/test_spec_drift.py` keeps the code honest against it.

## Public API

Shared primitives (roadmap step 0.5):

- **Ids** (`waggle.ids`, encoding in `waggle.ulid`):
  `IdKind` and thirteen `NewType` id types (`HiveId`, `CellId`, `LeaseId`, `TaskId`, `WorkerId`,
  `WardenId`, `AlarmId`, `GrantId`, `ToolId`, `NodeId`, `EventId`, `DeviceId`, `MessageId`), each
  generated as a prefixed ULID (`cell_01H...`) so an id is self-describing in a log line and sorts
  lexicographically by creation time. `new_id` and the thirteen `new_<kind>_id` wrappers mint
  one; `parse_id` validates a candidate string; `timestamp_of` reads its creation time back out.
- **Clock** (`waggle.clock`): the `Clock` protocol every time-reading component depends on,
  `SystemClock` (the real clock) and `FakeClock` (a clock a test drives by hand with `advance()`).
- **TickLoop** (`waggle.loop`): the standard long-running loop shape from codingrules section 11,
  with capped exponential backoff on recoverable errors, that the Queen, every Warden, every
  Worker and the Pollen gateway all subclass.
- **Errors** (`waggle.errors`): `WaggleError`, the package's own root (it cannot inherit from
  `hivemind.common.errors.HiveMindError`, since waggle may not import hivemind), and the protocol
  error tree (`InvalidIdError`; `CodecError` with `MalformedFrameError`, `FrameTooLargeError`,
  `UnsupportedVersionError`, `UnknownKindError`, `InvalidPayloadError`; `SignatureError` with
  `MissingSignatureError`, `UnknownSignerError`, `InvalidSignatureError`; `TransportError` with
  `TransportClosedError`, `ConnectionLostError`, `ConnectFailedError`; `OutboxError` with
  `OutboxCorruptError`), each with a stable `code` string that is the same string an
  `ErrorMessage` carries on the wire (spec section 7).

The protocol (roadmap phase 1):

- **Envelope** (`waggle.envelope`): the frozen ten-field wrapper (`id`, `correlation_id`,
  `sender`, `recipient`, `kind`, `version`, `sent_at`, `node_id`, `payload`, `signature`),
  `wrap(payload, hop, *, clock, correlation_id=None)` with `Hop(sender, recipient, node_id)`, and
  the `PROTOCOL_VERSION` constants. The envelope's model validator enforces the shape rule
  (a reply carries its request's id, a request carries none).
- **Codec** (`waggle.codec`): `Codec(signer=..., verifier=...)`
  with `encode(envelope) -> bytes` and `decode(frame) -> Envelope`, `canonical_bytes` (the sorted
  compact ASCII JSON that is signed, computed over the raw wire dict) and `MAX_FRAME_BYTES`
  (1 MiB). A verifier configured means every frame must be signed; none means signatures are
  ignored, the in-process case. `Signer` and `Verifier` are structural protocols, so the codec
  never imports the signing module. The key-free decode steps (`parse_frame`, `check_version`,
  `check_kind`, `build_envelope`) live beside `Codec`, which composes them in the spec's order.
- **Signing** (`waggle.signing`): `Ed25519Signer` (one per
  node, built from a key in the secret store or `generate()`) and `Ed25519Verifier` (an immutable
  map of `node_id` to raw public key, extended with `with_key`); `public_key_hex` and
  `public_key_from_hex` for the manifest form.
- **Messages** (`waggle.messages`): the catalogue of sixty-six kinds in ten families, one
  package each, and the registry that maps each kind to its class and shape; see
  `messages/README.md`.
- **Transports** (`waggle.transport`): the `Transport` protocol, `MemoryTransport` for one process
  and the WebSocket client and server for everything else; see `transport/README.md`.
- **Outbox** (`waggle.outbox`, a package: `queue`, `log`, `replay`): `Outbox(path)`, the
  append-only JSONL queue of unsigned envelopes a
  node could not send, and `replay_outbox(outbox, transport)`, which sends them in order after a
  reconnection, signing at send time, acking each after a successful send and stopping at the
  first transport error so the tail stays pending.
- **URIs** (`waggle.uris`): `check_waggle_uri`, the one rule for a dialable endpoint (`wss://`, or
  `ws://` only on a loopback host).

`scripts/waggle_echo.py` is the phase's demo: a WebSocket server in one process and a client in
another exchange `Ping`/`Pong` and a signed `TaskAssign`, reject a tampered envelope, and replay
the client's outbox after the server is killed and restarted.

## How to test this

```bash
uv run --frozen pytest packages/waggle/tests
```

Coverage floor is 95% (codingrules section 14.1):

```bash
COVERAGE_FILE=.coverage.waggle uv run --frozen pytest -p no:cacheprovider --cov=waggle \
    --cov-report=term-missing packages/waggle/tests
```

`FakeClock` (in `waggle.clock`, not under `tests/`) is what makes `TickLoop`'s backoff tests and
the WebSocket client's reconnect tests run instantly and deterministically: a test drives time
forward with `advance(seconds)` instead of waiting on a real timer. The transport conformance
suite under `tests/contracts/` runs every contract over both transports; `tests/README.md`
describes the rest of the tree.
