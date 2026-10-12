/**
 * Tests for the Android share intake adapter.
 *
 * The Kotlin plugin raises a `shared` event; the adapter decodes it (a clip or image arrives as
 * base64) into a `SharedItem` for the confirmation card. A malformed payload must surface as a
 * refused item, and stopping the listener must work even before the plugin's handle has arrived.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises platform/android/share.ts.
 *
 * Key invariants:
 *   - Bytes round-trip exactly; text is delivered as is.
 *   - After the returned stop function runs, no further item is delivered.
 *
 * See Also:
 *   - android/.../share/SharedItem.kt for the payload's shape.
 */
import { describe, expect, it } from 'vitest';
import type { SharedItem } from '../../../../../src/landing_board/client';
import type {
  ShareIntakePlugin,
  SharedPayload,
} from '../../../../../src/landing_board/client/platform/android/plugins';
import { androidShareIntake } from '../../../../../src/landing_board/client/platform/android/share';
import { flush } from '../../../../support/fake_browser';

class FakeShare implements ShareIntakePlugin {
  listener: ((payload: SharedPayload) => void) | null = null;
  removed = 0;

  addListener(_event: 'shared', listener: (payload: SharedPayload) => void) {
    this.listener = listener;
    return Promise.resolve({
      remove: () => {
        this.removed += 1;
        return Promise.resolve();
      },
    });
  }
}

async function collect(plugin: FakeShare): Promise<{ items: SharedItem[]; stop: () => void }> {
  const items: SharedItem[] = [];
  const stop = androidShareIntake(plugin).onShared((item) => items.push(item));
  await flush();
  return { items, stop };
}

describe('android share intake', () => {
  it('delivers shared text', async () => {
    const plugin = new FakeShare();
    const { items } = await collect(plugin);
    plugin.listener!({ kind: 'text', text: 'hello', mediaType: 'text/plain' });
    expect(items).toEqual([{ kind: 'text', text: 'hello' }]);
  });

  it('decodes an image and a clip to bytes under their media types', async () => {
    const plugin = new FakeShare();
    const { items } = await collect(plugin);
    plugin.listener!({ kind: 'image', base64: btoa('\u0001\u0002\u0003'), mediaType: 'image/png' });
    plugin.listener!({ kind: 'audio', base64: btoa('abc'), mediaType: 'audio/mp4' });
    expect(items[0]).toMatchObject({ kind: 'image', mediaType: 'image/png' });
    expect([...(items[0] as { bytes: Uint8Array }).bytes]).toEqual([1, 2, 3]);
    expect(items[1]).toMatchObject({ kind: 'audio', mediaType: 'audio/mp4' });
    expect([...(items[1] as { bytes: Uint8Array }).bytes]).toEqual([97, 98, 99]);
  });

  it('delivers a refusal from the native side and a malformed payload as refused', async () => {
    const plugin = new FakeShare();
    const { items } = await collect(plugin);
    plugin.listener!({ kind: 'refused', reason: 'too_large' });
    plugin.listener!({ kind: 'image', mediaType: 'image/png' });
    expect(items).toEqual([
      { kind: 'refused', reason: 'too_large' },
      { kind: 'refused', reason: 'unreadable' },
    ]);
  });

  it('removes the plugin listener when stopped, also before the handle arrives', async () => {
    const plugin = new FakeShare();
    const { stop } = await collect(plugin);
    stop();
    await flush();
    expect(plugin.removed).toBe(1);

    const early = new FakeShare();
    const stopEarly = androidShareIntake(early).onShared(() => undefined);
    stopEarly();
    await flush();
    expect(early.removed).toBe(1);
  });
});
