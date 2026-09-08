# waggle.transport

The transport package defines the `Transport` protocol that carries Envelopes (the outer wrapper
every Waggle message travels in) between two bees, plus the implementations that satisfy it. A
transport is a policy-free carrier of frames: every check on a frame (size, shape, version,
kind, signature) happens in the `Codec` it is built with. Delivery at this layer is at-most-once,
with ordering preserved within one connection; retries and durability belong to the caller
through `waggle.outbox`.

## Public API (roadmap steps 1.4 to 1.6)

- **`Transport`** (`base.py`): `connect()`, `send(envelope)`, `receive()` (an async generator:
  `async for envelope in transport.receive()`), `close()` and `is_connected`. The docstring is the
  contract of `docs/waggle/spec.md` section 9. `close_code_for(error)` maps a decode failure to
  the WebSocket close code both implementations use (1002 malformed, unsupported major or unknown
  kind; 1008 signature policy; 1009 too large); `InvalidPayloadError` closes nothing, and
  `receive()` may be called again on the same connection.
- **`MemoryTransport`** (`memory.py`): `MemoryTransport.pair(codec_a, codec_b)` returns two
  connected ends that exchange bytes through their codecs, so size, malformed and signature
  checks are real in-process and the memory transport is a faithful stand-in for the WebSocket
  one. `inject_frame(frame)` delivers a raw frame as if the peer sent it; `drop()` simulates link
  loss. Used by tests and by the Warden and Workers that run inside the Queen's process.
- **`WebSocketTransport`** (`websocket.py`): one open `websockets` connection, client or server
  side, binary frames only; keepalive is the WebSocket ping (`PING_INTERVAL_S`, `PING_TIMEOUT_S`).
- **`WebSocketClientTransport`** (`websocket_client.py`): dials a `wss://` URI (or `ws://` on a
  loopback host) with capped exponential backoff through the injected `Clock`
  (`RECONNECT_INITIAL_S`, `RECONNECT_FACTOR`, `RECONNECT_MAX_S`) and raises `ConnectFailedError`
  after `max_attempts`. There is no automatic reconnect inside `receive()`: after a drop the
  caller calls `connect()` again and replays its outbox, so every retry is explicit.
- **`WebSocketServer`** (`websocket_server.py`): the loopback-by-default listener; `start()`,
  `port`, `uri`, `connections()` (an async generator of accepted `WebSocketTransport`s) and
  `close()`. Every Cell and device dials out to the Hive Stand; the server is the only listener.

## How to test this

```bash
uv run --frozen pytest packages/waggle/tests/transport packages/waggle/tests/contracts
```

`tests/contracts/test_transport_contract.py` is the conformance suite of roadmap step 1.9,
parametrised over both implementations: ordering within a connection, close semantics, oversized
and malformed frame rejection, signature rejection, and outbox replay after a dropped link. The
WebSocket tests bind a real loopback server on an OS-assigned port and use a raw `websockets`
client to send the frames a well-behaved transport never would.
