/**
 * Tests for sending a confirmed shared item.
 *
 * Text goes through the chat's `send`, a clip through its `speak` as a message under a format the
 * Hive takes and with a measured length, and an image through the image intake call under the
 * step-up guard. Anything the Hive cannot take is refused before a request.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/share/intake/send_item.ts with recording fakes.
 *
 * Key invariants:
 *   - Each kind uses exactly one route and nothing else is called.
 *   - A refusal is a `LandingBoardError` made on the device (status 0).
 *
 * See Also:
 *   - src/views/chat/writes/use_actions.ts.
 */
import { describe, expect, it } from 'vitest';
import { LandingBoardError } from '../../src/landing_board/client';
import type { SharedItem } from '../../src/landing_board/client';
import { sendShared } from '../../src/share/intake/send_item';
import type { SendDeps } from '../../src/share/intake/send_item';

function recorder() {
  const calls: unknown[][] = [];
  const deps: SendDeps = {
    actions: {
      send: (text) => {
        calls.push(['send', text]);
        return Promise.resolve();
      },
      speak: (clip, intent) => {
        calls.push(['speak', clip, intent]);
        return Promise.resolve();
      },
    },
    guarded: (action) => {
      calls.push(['guarded']);
      return action();
    },
    http: {
      postImage: (image) => {
        calls.push(['postImage', image]);
        return Promise.resolve();
      },
    },
    measure: () => Promise.resolve(12.5),
  };
  return { calls, deps };
}

const BYTES = Uint8Array.from([1, 2, 3]);

describe('sendShared', () => {
  it('sends text through the chat send action', async () => {
    const { calls, deps } = recorder();
    await sendShared({ kind: 'text', text: 'note to self' }, deps);
    expect(calls).toEqual([['send', 'note to self']]);
  });

  it('sends a clip as a chat message in a format the Hive takes, with its measured length', async () => {
    const { calls, deps } = recorder();
    await sendShared({ kind: 'audio', bytes: BYTES, mediaType: 'audio/x-m4a' }, deps);
    expect(calls).toEqual([
      ['speak', { bytes: BYTES, mediaType: 'audio/mp4', durationS: 12.5 }, 'chat'],
    ]);
  });

  it('refuses a clip in a format the Hive does not take, before any request', async () => {
    const { calls, deps } = recorder();
    const failure = await sendShared({ kind: 'audio', bytes: BYTES, mediaType: 'audio/amr' }, deps)
      .then(() => null)
      .catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(LandingBoardError);
    expect((failure as LandingBoardError).status).toBe(0);
    expect(calls).toEqual([]);
  });

  it('sends an image through the image intake route under the step-up guard', async () => {
    const { calls, deps } = recorder();
    await sendShared({ kind: 'image', bytes: BYTES, mediaType: 'image/jpeg' }, deps);
    expect(calls).toEqual([['guarded'], ['postImage', { bytes: BYTES, mediaType: 'image/jpeg' }]]);
  });

  it('refuses an image in a format the Hive does not take, and a refused item', async () => {
    const { calls, deps } = recorder();
    const gif: SharedItem = { kind: 'image', bytes: BYTES, mediaType: 'image/gif' };
    await expect(sendShared(gif, deps)).rejects.toBeInstanceOf(LandingBoardError);
    await expect(sendShared({ kind: 'refused', reason: 'too_large' }, deps)).rejects.toBeInstanceOf(
      LandingBoardError,
    );
    expect(calls).toEqual([]);
  });
});
