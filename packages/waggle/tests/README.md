# waggle tests

Tests for the waggle package: the Hive's shared wire protocol (the envelope, the codec, signing,
the message catalogue, the transports and the offline outbox) and its shared primitives (ids,
the clock and the long-running loop shape). The tree mirrors `src/waggle/` one test module per
source module (a large module's suite splits by feature: `test_codec_decode.py`,
`test_ids_minting.py`), with four subdirectories:

- `messages/`: one directory per family mirroring `src/waggle/messages/<family>/`, one test
  module per source module, the family's root test module carrying an `EXAMPLES` tuple with one
  valid instance of every class; `test_registry*.py` loads all ten by file path and round-trips
  every example through the codec.
- `outbox/`: the queue, the JSONL log underneath it and the replay through a transport.
- `transport/`: the memory and WebSocket transports, the latter against a real loopback server.
- `contracts/`: the transport conformance suite of roadmap step 1.9, parametrised over both
  implementations.

At the top level, `test_spec_drift.py` parses the catalogue table in `docs/waggle/spec.md` and
checks it against the registry in both directions, and `test_dependencies.py` proves no module
under `src/waggle/` imports anything beyond the standard library, `pydantic`, `websockets` and
`cryptography`, which is what lets `pollen` depend on waggle alone. `conftest.py` is the test
composition root: a `FakeClock`, ids and bee addresses, an envelope factory, and a keypair with
the plain and signed codecs built on it.

```bash
uv run --frozen pytest packages/waggle/tests
```
