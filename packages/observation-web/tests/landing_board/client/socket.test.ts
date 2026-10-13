/**
 * Tests for the stream wrapper: first frame, frames, and the reconnect policy by close code.
 *
 * The first frame must be a correctly signed `SocketHello`, sent before anything else and well
 * inside the deadline. After that the policy of ADR-0049 is checked code by code, with a manual
 * clock: 4409 resumes from the cursor at once, 4410 and 4411 end the session, 4401 and 4403 are
 * refusals that are not retried, and everything else backs off 1, 2, 4, 8, then 30 seconds with
 * a reset once a connection is accepted.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/landing_board/client/socket.ts.
 *
 * Key invariants:
 *   - Time moves only when a test says so; no real timer is involved.
 *
 * See Also:
 *   - docs/adr/0049-observation-hive-dashboard-stack.md (reconnect policy).
 *   - docs/entrance/openapi.json, `x-hive-socket-close`.
 */
import { describe, expect, it } from 'vitest';
import { createHolder } from '../../../src/landing_board/client/keys';
import { socketString } from '../../../src/landing_board/client/signing';
import {
  backoffDelay,
  HELLO_DEADLINE_MS,
  openStream,
  policyFor,
} from '../../../src/landing_board/client/socket';
import type { StreamEnd, StreamState } from '../../../src/landing_board/client/socket';
import { createFakeBrowser, flush } from '../../support/fake_browser';
import { makeSession, verifies } from '../../support/session_fixture';

interface Frame {
  seq: number;
}

async function harness() {
  const fake = createFakeBrowser();
  const holder = createHolder();
  const session = await makeSession();
  holder.set(session);
  const frames: Frame[] = [];
  const states: StreamState[] = [];
  const ends: StreamEnd[] = [];
  const handle = openStream<Frame>(
    { browser: fake.browser, holder },
    {
      path: '/v1/chat/stream',
      onFrame: (frame) => frames.push(frame),
      onState: (state) => states.push(state),
      onEnd: (end) => ends.push(end),
      resume: { cursorOf: (frame) => String(frame.seq), query: (cursor) => `?after=${cursor}` },
    },
  );
  return { fake, session, frames, states, ends, handle };
}

describe('first frame', () => {
  it('is a signed hello, sent first and inside the deadline', async () => {
    const { fake, session } = await harness();
    const socket = fake.sockets[0]!;
    expect(socket.url).toBe('wss://hive.test/v1/chat/stream');
    // Nothing is sent before the socket is open.
    expect(socket.sent).toHaveLength(0);
    socket.open();
    await flush();
    expect(socket.sent).toHaveLength(1);
    const hello = JSON.parse(socket.sent[0]!) as Record<string, unknown>;
    expect(Object.keys(hello).sort()).toEqual(['nonce', 'signature', 'timestamp', 'token']);
    expect(hello.token).toBe(session.token);
    // It was sent at the moment of opening, so far inside the 5 s deadline.
    expect(hello.timestamp).toBe(Math.floor(fake.now() / 1000));
    expect(fake.now() - 1_767_225_600_000).toBeLessThan(HELLO_DEADLINE_MS);
    const signed = socketString({
      target: '/v1/chat/stream',
      timestamp: hello.timestamp as number,
      nonce: hello.nonce as string,
    });
    expect(await verifies(session.publicKey, hello.signature as string, signed)).toBe(true);
  });

  it('sends nothing else on its own, even when frames arrive', async () => {
    const { fake } = await harness();
    const socket = fake.sockets[0]!;
    socket.open();
    await flush();
    socket.receive({ seq: 1 });
    expect(socket.sent).toHaveLength(1);
  });
});

describe('frames and acceptance', () => {
  it('parses frames, ignores junk, and is live from the first frame', async () => {
    const { fake, frames, states } = await harness();
    const socket = fake.sockets[0]!;
    socket.open();
    socket.receive('not json');
    socket.receive('42');
    expect(frames).toEqual([]);
    socket.receive({ seq: 1 });
    expect(frames).toEqual([{ seq: 1 }]);
    expect(states).toEqual(['connecting', 'live']);
  });

  it('counts the connection accepted when the hello deadline passes with the socket open', async () => {
    const { fake, states } = await harness();
    fake.sockets[0]!.open();
    await flush();
    fake.advance(HELLO_DEADLINE_MS - 1);
    expect(states).toEqual(['connecting']);
    fake.advance(1);
    expect(states).toEqual(['connecting', 'live']);
  });
});

describe('close codes', () => {
  it('maps each code to its policy', () => {
    expect(policyFor(4409)).toBe('resume');
    expect(policyFor(4410)).toBe('session_ended');
    expect(policyFor(4411)).toBe('reduced');
    expect(policyFor(4401)).toBe('refused');
    expect(policyFor(4403)).toBe('refused');
    expect(policyFor(1000)).toBe('finished');
    for (const code of [1001, 1008, 1006]) expect(policyFor(code)).toBe('backoff');
  });

  it('resumes from the last cursor at once on 4409', async () => {
    const { fake, states, session } = await harness();
    fake.sockets[0]!.open();
    fake.sockets[0]!.receive({ seq: 7 });
    fake.sockets[0]!.serverClose(4409);
    expect(states.at(-1)).toBe('reconnecting');
    fake.advance(0);
    const resumed = fake.sockets[1]!;
    expect(resumed.url).toBe('wss://hive.test/v1/chat/stream?after=7');
    resumed.open();
    await flush();
    // The hello signs the path and query exactly as requested.
    const hello = JSON.parse(resumed.sent[0]!) as {
      timestamp: number;
      nonce: string;
      signature: string;
    };
    const signed = socketString({
      target: '/v1/chat/stream?after=7',
      timestamp: hello.timestamp,
      nonce: hello.nonce,
    });
    expect(await verifies(session.publicKey, hello.signature, signed)).toBe(true);
  });

  it('ends the session on 4410 and 4411 and never reconnects', async () => {
    for (const [code, reason] of [
      [4410, 'session_ended'],
      [4411, 'reduced'],
    ] as const) {
      const { fake, ends } = await harness();
      fake.sockets[0]!.open();
      fake.sockets[0]!.serverClose(code);
      fake.advance(120_000);
      expect(ends).toEqual([{ reason, code }]);
      expect(fake.sockets).toHaveLength(1);
    }
  });

  it('shows the refusal and does not retry on 4401 and 4403', async () => {
    for (const code of [4401, 4403]) {
      const { fake, ends, states } = await harness();
      fake.sockets[0]!.open();
      fake.sockets[0]!.serverClose(code);
      fake.advance(120_000);
      expect(ends).toEqual([{ reason: 'refused', code }]);
      expect(states.at(-1)).toBe('refused');
      expect(fake.sockets).toHaveLength(1);
    }
  });

  it('does not retry a normal close (1000)', async () => {
    const { fake, states } = await harness();
    fake.sockets[0]!.open();
    fake.sockets[0]!.serverClose(1000);
    fake.advance(120_000);
    expect(states.at(-1)).toBe('closed');
    expect(fake.sockets).toHaveLength(1);
  });
});

describe('backoff', () => {
  it('waits 1, 2, 4, 8 then 30 seconds between attempts', () => {
    expect([0, 1, 2, 3, 4, 5, 9].map(backoffDelay)).toEqual([
      1000, 2000, 4000, 8000, 30000, 30000, 30000,
    ]);
  });

  it('retries on network loss with that sequence, and resets once accepted', async () => {
    const { fake } = await harness();
    let attempts = 1;
    for (const seconds of [1, 2, 4, 8, 30, 30]) {
      fake.sockets.at(-1)!.serverClose(1006);
      // One millisecond short of the delay: no new socket yet.
      fake.advance(seconds * 1000 - 1);
      expect(fake.sockets).toHaveLength(attempts);
      fake.advance(1);
      attempts += 1;
      expect(fake.sockets).toHaveLength(attempts);
    }
    // An accepted frame resets the sequence to 1 second.
    fake.sockets.at(-1)!.open();
    fake.sockets.at(-1)!.receive({ seq: 1 });
    fake.sockets.at(-1)!.serverClose(1001);
    fake.advance(1000);
    expect(fake.sockets).toHaveLength(attempts + 1);
  });

  it('backs off the same way on 1008', async () => {
    const { fake } = await harness();
    fake.sockets[0]!.serverClose(1008);
    fake.advance(999);
    expect(fake.sockets).toHaveLength(1);
    fake.advance(1);
    expect(fake.sockets).toHaveLength(2);
  });
});

describe('close()', () => {
  it('stops for good: closes the socket, clears timers, never reconnects', async () => {
    const { fake, handle, states } = await harness();
    fake.sockets[0]!.open();
    await flush();
    handle.close();
    expect(fake.sockets[0]!.closedWith).toBe(1000);
    // Even a late close event from the dead socket is ignored.
    fake.sockets[0]!.serverClose(1006);
    fake.advance(120_000);
    expect(fake.sockets).toHaveLength(1);
    expect(fake.pendingTimers()).toBe(0);
    expect(states.at(-1)).toBe('closed');
  });
});
