# ADR-0003: Ids, clock and loop live in waggle

- Status: Accepted
- Date: 2026-09-07

## Context

Every message that crosses Waggle (the shared messaging protocol between bees, named after the
honeybee's waggle dance) is wrapped in an envelope that carries an id, and every long-running
process in the system, the Queen, a Warden, a Worker and Pollen (the lightweight device connector
that runs on a newly enrolled device) alike, needs to know what time it is and needs to run the
same kind of forever-loop. Pollen must install on constrained hardware such as a Raspberry Pi with
no Docker, no SQLite extensions and no LLM SDK, and per ADR-0002 it may depend on nothing in the
workspace beyond `waggle`; since Pollen's own agent loop needs ids, a clock and a loop shape just
as much as anything inside `hivemind` does, those three primitives cannot live inside `hivemind`
without either duplicating them in `pollen` or giving Pollen a dependency it is not allowed to
have. Phase 1's exit criterion requires Pollen to run with only `pydantic`, `websockets` and
`cryptography` installed, which rules out any third-party ULID or UUID library for this piece of
the system. Deterministic testing is a hard requirement across the whole codebase: a test that
reads wall-clock time or that sleeps on a real timer is either slow or flaky, and both are
unacceptable in a suite that has to run in CI on Windows, Ubuntu and Arch. Finally, codingrules
section 11 requires every long-running loop (`Queen.run`, `Worker.run`, `PollenPacket.run`) to
follow the same documented shape, and repeating that shape by hand in each subsystem is exactly the
kind of drift a shared base class exists to prevent.

## Decision

Ids, the clock and the long-running loop shape all live in `waggle`, as `waggle/ids.py`,
`waggle/clock.py` and `waggle/loop.py`, implemented with the standard library only. An id is a
lowercase type prefix and an underscore (`hive_`, `cell_`, `lease_`, `task_`, `worker_`, `warden_`,
`alarm_`, `grant_`, `tool_`, `node_`, `event_`, `device_`) followed by a 26-character ULID: a
48-bit millisecond timestamp and 80 random bits drawn from the standard library's `secrets`
module, rendered in Crockford base32, so an id is self-describing wherever it appears in a log line
and ids sort lexicographically by creation time without decoding them. Every id type is its own
`NewType` over `str`, so a `TaskId` can never be passed where a `CellId` is expected without mypy
catching it. The `Clock` protocol is three members: `now()` returning a timezone-aware UTC
`datetime`, `monotonic()` for measuring elapsed time, and an `async def sleep(seconds)`; it is
injected into every component that reads time, never read from a module-level global, so that a
test can substitute a `FakeClock` and get deterministic behaviour. `waggle/clock.py` ships both
`SystemClock` (backed by the real `datetime`, `time.monotonic` and `asyncio.sleep`) and
`FakeClock`, whose `sleep()` only resolves once a test explicitly advances the fake clock's time;
`FakeClock` lives in `src/waggle/`, not under `tests/`, because Pollen and `hive doctor` (the
system's built-in diagnostic command) both use it outside of test code. `waggle/loop.py` defines a
small base class implementing the shape from codingrules section 11: `run()` loops on
`while not self._stop.is_set()`, calling an abstract `_tick()` once per iteration; `stop()` sets the
stop flag; a subclass declares which errors are recoverable, and anything else propagates;
`asyncio.CancelledError` is always re-raised, never swallowed; backoff after a recoverable error
starts at 0.5 seconds, doubles on each consecutive failure, caps at 30 seconds, and resets to 0.5
seconds after the next successful tick; every sleep in the loop, including backoff, goes through
the injected `Clock`; and the base class exposes an overridable failure hook that a subclass in
`hivemind` overrides to write a `PheromoneEvent` to the Pheromone Trail (the system's append-only
audit log), which `waggle/loop.py` itself cannot do, since `waggle` may not import `hivemind`.

## Consequences

Positive: Pollen gets ids, a clock and the standard loop shape without gaining a single dependency
beyond `waggle`, so it keeps installing on constrained hardware exactly as ADR-0002 requires. An id
carrying its own type prefix means a log line or a database row is self-describing without a schema
lookup, and lexicographic sort-by-creation-time falls out of the ULID's structure for free, which
is useful for the Pheromone Trail's ordering guarantees. A `NewType` per id family turns "passed a
`CellId` where a `TaskId` was expected" from a runtime bug into a `mypy --strict` failure at review
time. Every subsystem that reads time takes a `Clock` instead of calling `datetime.now()` or
`asyncio.sleep()` directly, so the whole test suite can run at simulated speed with a `FakeClock`
and stay deterministic across Windows, Ubuntu and Arch CI. One loop base class means the backoff
policy, the cancellation handling and the failure-hook shape are each written once and reused by
the Queen, every Warden, every Worker and Pollen's own agent loop, rather than reimplemented with
small, drifting differences in each.

Negative: hand-rolling ULID generation and Crockford base32 encoding against the standard library
alone, rather than reaching for a maintained third-party library, means HiveMind owns the
correctness of that encoding and any future spec nuance itself, including tests for edge cases a
published library would already have covered. A `NewType` per id family adds a small amount of
boilerplate at every call site that constructs or narrows an id, and a developer who reaches for a
bare `str` instead of the `NewType` will not be caught by anything except code review and the
`NewType` boundary itself, since `NewType` erases at runtime. Requiring a `Clock` to be injected
everywhere is a constructor-signature tax paid by every time-reading component, including small
ones where a bare `datetime.now()` would have been simpler to write and read. The loop base class's
generality, recoverable-versus-fatal errors declared by the subclass, a failure hook the subclass
overrides, means a new subclass has to understand the base class's contract before its error
handling behaves correctly, which is a small extra concept for a new contributor to learn before
touching the Queen's or a Warden's `run()` method.

## Alternatives considered

The `python-ulid` or `uuid-utils` third-party libraries: either would have saved writing the ULID
encoding by hand, but both are disqualified by phase 1's exit criterion that Pollen run with only
`pydantic`, `websockets` and `cryptography` installed; pulling in a fourth dependency for ids alone
would have broken that criterion for every device Pollen runs on.

UUID4: universally available in the standard library and needs no custom encoding, but a UUID4
carries no type information and does not sort by creation time, which would have taken away both
the self-describing-in-logs property and the natural chronological ordering the Pheromone Trail
relies on.

Integer ids: simplest possible representation and trivially sortable, but they require a
centralized counter or a database identity column to avoid collisions, which does not fit a system
where a Warden can be offline and minting ids of its own before it ever reconnects to the Queen.

A separate `hivemind-primitives` package for ids, the clock and the loop: would have kept `waggle`
narrower, but it would have added a fifth workspace package for three small modules that Waggle's
own envelopes already need directly, and Pollen would then depend on two packages instead of one
for no isolation benefit `waggle` does not already provide.

Reading time from a global clock instead of an injected `Clock`: simpler call sites with no
constructor parameter to thread through, but it makes every test that touches timing either slow
(real sleeps) or flaky (racing real wall-clock time), which is exactly the outcome dependency
injection of the `Clock` protocol exists to prevent.
