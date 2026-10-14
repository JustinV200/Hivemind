# waggle.transport

The transport package defines the `Transport` protocol that carries Envelopes (the outer wrapper
every Waggle message travels in) between two bees, plus the implementations that satisfy it. A
transport is a policy-free carrier of frames: every check on a frame (size, shape, version,
kind, signature) happens in the `Codec` it is built with. Delivery at this layer is at-most-once,
with ordering preserved within one connection; retries and durability belong to the caller
through `waggle.outbox`. The one place a transport has a policy is the Hive Stand's listener,
which defends itself (roadmap steps 11.3a and 11.3b): a frame ceiling per connection, a replay
window shared by all of them, and `wss://` with a certificate a dialling node pins. The sender's
half of the duplicate story is `recent/`: a node whose connection died without anyone noticing sends
again, under the same envelope ids, the frames it sent in the stretch the dead link swallowed.

## Public API (roadmap steps 1.4 to 1.6, 10.3a, 11.3a, 11.3b and 11.9)

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
  loopback host or a v3 onion service) with capped exponential backoff through the injected
  `Clock` (`RECONNECT_INITIAL_S`, `RECONNECT_FACTOR`, `RECONNECT_MAX_S`) and raises
  `ConnectFailedError` after `max_attempts`. There is no automatic reconnect inside `receive()`:
  after a drop the caller calls `connect()` again and replays its outbox, so every retry is
  explicit. With `DialOptions.socks_proxy_url` (roadmap step 10.3a, a Night Veil Cell's Tor
  SOCKS port) every attempt goes through that proxy and never directly; an onion URI with no
  proxy is refused at construction.
- **SOCKS** (`socks/`, a package): `open_socks_connection(proxy, host, port, timeouts)` runs
  RFC 1928 SOCKS5 (no authentication, the destination named, `socks5h`) or SOCKS4a against a
  loopback proxy (`SocksProxy.parse`), bounded by `SocksTimeouts`, and returns the connected
  socket the WebSocket handshake runs over; every failure is `ProxyFailedError`.
  `FakeSocksProxy` (`socks/fake.py`) plays Tor's SOCKS port on loopback for tests, relaying each
  named destination to a route, and misbehaves on request (`FakeSocksBehaviour`).
- **`WebSocketServer`** (`websocket_server.py`): the loopback-by-default listener; `start()`,
  `port`, `uri`, `connections()` (an async generator of accepted `WebSocketTransport`s) and
  `close()`. Every Cell and device dials out to the Hive Stand; the server is the only listener.
  `WebSocketServer(codec, host=..., port=..., **options)` takes its optional settings as the
  typed keywords of `ServerOptions` (so the constructor stays within the five-parameter limit):
  `limits` (a `ServerLimits`), `ssl_context` (serve `wss://`; `uri` then says so), `on_refusal`
  (an async callback handed each `ConnectionRefusal`), `clock` and `open_timeout_s`. Without
  `limits` and `ssl_context` the server is exactly the plain listener it always was.
- **Limits** (`limits.py`, `replay.py`, `ceiling.py`, roadmap steps 11.3a and 11.3b): with
  `ServerLimits` every accepted connection counts each received frame against its own
  `FrameCeiling`, a pure token bucket (`CeilingLimits`: `max_frames_per_minute` 6000, `burst`
  600), *before* the frame is parsed; over it the listener reports a `FRAME_CEILING` refusal,
  closes with code 4029 (`CLOSE_FRAME_CEILING`, reason `waggle.transport.frame_ceiling`) and
  `receive()` raises `FrameCeilingError`. Every decoded envelope's `(node_id, id)` is checked
  against one `ReplayWindow` (`ReplayLimits`: `window_s` 600, `max_ids` 50,000) shared by all
  connections, so a signed frame replayed on a fresh connection is caught; a duplicate is
  reported as a `REPLAY` refusal and skipped, never yielded. Both checks are pure and take the
  time as an argument (read from the injected `Clock`), so tests need no timer. Waggle writes no
  trail: the Hive Stand's listener wrapper records each `ConnectionRefusal` (`node_id`, `kind`,
  `envelope_id`, `at`) as `guard.frame_ceiling` or `guard.replay_refused`.
  `WebSocketTransport` consults the listener through `ReceiveHooks`, which `ConnectionLimiter`
  satisfies; a dialled connection has no hooks.
- **Recent frames** (`recent/`, a package; roadmap step 11.9): a `send` that returns has put a
  frame on a connection, not in the receiver's hands, and a cable pulled out from under a node
  leaves the connection looking alive until the keepalive gives up (`PING_INTERVAL_S +
  PING_TIMEOUT_S`, forty seconds), so every frame sent in that time returns as sent and is lost.
  `RecentFrames` (`ring.py`) is the pure, bounded memory of what a node sent lately: at most
  `MAX_RECENT_FRAMES` (256) and `MAX_RECENT_BYTES` (4 MiB), oldest first, none from more than
  `UNNOTICED_S` (80 s, twice the keepalive) before the newest frame or before the link was lost,
  and `due(lost_at, now)` offers only frames the receiver's replay window still remembers
  (`RESEND_HORIZON_S`, a minute short of `REPLAY_WINDOW_S`), because one she had handled and
  forgotten would be handled twice. `RememberingTransport` (`remembering.py`) wraps a `Transport`:
  it remembers each frame its node says must arrive once the inner `send` returned, and `resend`
  sends the frames due again on a new connection, oldest first, under their own ids (the
  receiver's window drops what it held). `clear` forgets everything when the peer closed with a
  close frame, which reads everything sent before it. Time is a reading of the injected `Clock`'s
  `monotonic`, passed in, so tests need no timer.
- **TLS** (`tls.py`): `server_ssl_context(cert, key)` builds the listener's context (TLS 1.2 or
  later); `DialOptions.tls_pin_sha256` makes a `wss://` dial trust one certificate by its
  SHA-256 fingerprint (`certificate_sha256`, 64 lowercase hex) instead of a certificate
  authority. Right after the handshake, before any frame is sent, `check_pin` compares the
  peer's certificate with the pin; a mismatch aborts the connection and raises
  `TlsPinMismatchError` (a `ConnectFailedError`, code `waggle.transport.tls_pin`) at once, never
  retried. It works through a SOCKS proxy too, and a pin on a `ws://` URI is refused. A `wss://`
  URI with no pin keeps the default certificate-authority verification.

## How to test this

```bash
uv run --frozen pytest packages/waggle/tests/transport packages/waggle/tests/contracts
```

`tests/contracts/test_transport_contract.py` is the conformance suite of roadmap step 1.9,
parametrised over both implementations: ordering within a connection, close semantics, oversized
and malformed frame rejection, signature rejection, and outbox replay after a dropped link. The
WebSocket tests bind a real loopback server on an OS-assigned port and use a raw `websockets`
client to send the frames a well-behaved transport never would. The limits tests
(`test_replay.py`, `test_ceiling.py` and `test_limits.py` for the pure parts,
`test_websocket_server_limits.py` for the 4029 close and a replay on a second connection) inject a
`FakeClock`; the TLS tests (`test_tls.py`, `test_websocket_tls.py`) generate throwaway
self-signed certificates with `cryptography` (see `tests/transport/conftest.py`), so no key is
committed.
