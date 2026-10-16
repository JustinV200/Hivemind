/**
 * Tests for the Android push adapter and the push client's logout behaviour.
 *
 * ADR-0050: the distributor's endpoint and Web Push keys are registered with the Entrance exactly
 * as a browser subscription is (`channel: web_push`), the VAPID key reaches the distributor as
 * unpadded base64url, and logging out withdraws the subscription and the distributor registration.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises platform/android/push.ts, client/push.ts and the
 *   logout wrapper in client/index.ts.
 *
 * Key invariants:
 *   - The body posted is the generated `SubscribeBody` shape with the plugin's endpoint and keys.
 *   - Leaving works with the Entrance unreachable, and still drops the distributor registration.
 *
 * See Also:
 *   - docs/entrance/landing-board.md sections 8.2 and 9.
 */
import type { PluginListenerHandle } from '@capacitor/core';
import { describe, expect, it } from 'vitest';
import { createLandingBoard } from '../../../../../src/landing_board/client';
import { androidPush } from '../../../../../src/landing_board/client/platform/android/push';
import type {
  PushRegistration,
  UnifiedPushPlugin,
} from '../../../../../src/landing_board/client/platform/android/plugins';
import { flush } from '../../../../support/fake_browser';
import { base64urlEncode } from '../../../../../src/landing_board/client/signing';
import { createFakeBrowser } from '../../../../support/fake_browser';
import { storeSession } from '../../../../support/session_fixture';

const REGISTRATION: PushRegistration = {
  endpoint: 'https://ntfy.vpn/up123',
  p256dh: 'BPUBLICKEY',
  auth: 'AUTHSECRET',
};

const ROTATED: PushRegistration = { endpoint: 'https://ntfy.vpn/up456', p256dh: 'BNEW', auth: 'NEW' };

class FakePlugin implements UnifiedPushPlugin {
  vapidKeys: string[] = [];
  unregistered = 0;
  failure: { code: string } | null = null;
  // Listeners by event, as the native side keeps them; `removed` counts handles given back.
  private listeners = new Map<string, ((payload: unknown) => void)[]>();
  removed = 0;

  register(options: { vapidKey: string }): Promise<PushRegistration> {
    this.vapidKeys.push(options.vapidKey);
    return this.failure ? Promise.reject(this.failure) : Promise.resolve(REGISTRATION);
  }

  unregister(): Promise<void> {
    this.unregistered += 1;
    return Promise.resolve();
  }

  addListener(event: string, listener: (payload: never) => void): Promise<PluginListenerHandle> {
    const list = this.listeners.get(event) ?? [];
    list.push(listener as (payload: unknown) => void);
    this.listeners.set(event, list);
    return Promise.resolve({
      remove: () => {
        this.removed += 1;
        this.listeners.set(event, list.filter((l) => l !== listener));
        return Promise.resolve();
      },
    });
  }

  // What the Kotlin receiver raises: a rotated endpoint, or the distributor dropping the app.
  emit(event: 'endpoint' | 'unregistered', payload: unknown = {}): void {
    for (const listener of this.listeners.get(event) ?? []) listener(payload);
  }
}

describe('android push adapter', () => {
  it('registers under the VAPID key and returns a browser-shaped subscription', async () => {
    const plugin = new FakePlugin();
    const subscription = await androidPush(plugin).subscribeWebPush(Uint8Array.from([1, 2, 3]));
    expect(plugin.vapidKeys).toEqual([base64urlEncode(Uint8Array.from([1, 2, 3]))]);
    expect(subscription).toEqual({
      endpoint: REGISTRATION.endpoint,
      keys: { p256dh: REGISTRATION.p256dh, auth: REGISTRATION.auth },
    });
  });

  it.each([
    ['NO_DISTRIBUTOR', /distributor is installed/],
    ['DENIED', /permission/],
    ['UNENCRYPTED', /encrypted Web Push/],
    ['TIMEOUT', /did not answer/],
    ['SOMETHING_ELSE', /could not be set up/],
  ])('turns a %s rejection into a sentence for the human', async (code, words) => {
    const plugin = new FakePlugin();
    plugin.failure = { code };
    await expect(androidPush(plugin).subscribeWebPush(Uint8Array.from([1]))).rejects.toThrow(words);
  });

  it('unregisters from the distributor, and a failing distributor does not matter', async () => {
    const plugin = new FakePlugin();
    await androidPush(plugin).unsubscribeWebPush();
    expect(plugin.unregistered).toBe(1);
    plugin.unregister = () => Promise.reject(new Error('gone'));
    await expect(androidPush(plugin).unsubscribeWebPush()).resolves.toBeUndefined();
  });

  it('relays the plugin events as push changes, and stops when told', async () => {
    const plugin = new FakePlugin();
    const changes: unknown[] = [];
    const stop = androidPush(plugin).pushChanges.onChange((change) => changes.push(change));
    await flush();
    plugin.emit('endpoint', ROTATED);
    plugin.emit('unregistered');
    expect(changes).toEqual([
      {
        kind: 'endpoint',
        subscription: { endpoint: ROTATED.endpoint, keys: { p256dh: 'BNEW', auth: 'NEW' } },
      },
      { kind: 'unregistered' },
    ]);
    stop();
    await flush();
    expect(plugin.removed).toBe(2);
    plugin.emit('unregistered');
    expect(changes).toHaveLength(2);
  });
});

// A signed-in board whose browser registers through the android adapter.
async function signedIn(plugin: FakePlugin) {
  const fake = createFakeBrowser();
  await storeSession(fake.browser);
  const push = androidPush(plugin);
  fake.browser.subscribeWebPush = push.subscribeWebPush;
  fake.browser.unsubscribeWebPush = push.unsubscribeWebPush;
  fake.browser.pushChanges = push.pushChanges;
  const board = createLandingBoard({ browser: fake.browser, onSessionLost: () => {} });
  await board.session.restore();
  fake.respond('GET /v1/push/vapid-key', 200, {
    application_server_key: base64urlEncode(Uint8Array.from([9, 9])),
  });
  fake.respond('POST /v1/push/subscriptions', 201, {
    id: 'sub_7',
    channel: 'web_push',
    created_at: 'x',
  });
  fake.respond('DELETE /v1/push/subscriptions/sub_7', 204);
  fake.respond('POST /v1/auth/logout', 204);
  return { fake, board };
}

describe('push client on android', () => {
  it('posts the distributor endpoint and keys as a web_push subscription', async () => {
    const { fake, board } = await signedIn(new FakePlugin());
    await board.push.register();
    const post = fake.requests.find((r) => r.method === 'POST' && r.target.endsWith('subscriptions'));
    expect(JSON.parse(new TextDecoder().decode(post!.body!))).toEqual({
      channel: 'web_push',
      endpoint: REGISTRATION.endpoint,
      keys: { p256dh: REGISTRATION.p256dh, auth: REGISTRATION.auth },
    });
    expect(await board.push.isRegistered()).toBe(true);
  });

  it('withdraws the subscription and the distributor registration at logout, before leaving', async () => {
    const plugin = new FakePlugin();
    const { fake, board } = await signedIn(plugin);
    await board.push.register();
    await board.session.logout();
    const sent = fake.requests.map((r) => `${r.method} ${r.target}`);
    expect(sent.slice(-2)).toEqual(['DELETE /v1/push/subscriptions/sub_7', 'POST /v1/auth/logout']);
    expect(plugin.unregistered).toBe(1);
    expect(await board.push.isRegistered()).toBe(false);
  });

  it('still drops the local registration when the Entrance cannot be reached', async () => {
    const plugin = new FakePlugin();
    const { fake, board } = await signedIn(plugin);
    await board.push.register();
    fake.respond('DELETE /v1/push/subscriptions/sub_7', 500, { error: 'x', detail: 'y' });
    await board.session.logout();
    expect(plugin.unregistered).toBe(1);
    expect(await board.push.isRegistered()).toBe(false);
  });

  it('makes no push request at logout when nothing was registered', async () => {
    const { fake, board } = await signedIn(new FakePlugin());
    await board.session.logout();
    expect(fake.requests.map((r) => r.method)).toEqual(['POST']);
  });

  it('re-registers a rotated endpoint, dropping the old subscription first', async () => {
    const plugin = new FakePlugin();
    const { fake, board } = await signedIn(plugin);
    await board.push.register();
    await flush();
    fake.respond('POST /v1/push/subscriptions', 201, { id: 'sub_8', channel: 'web_push', created_at: 'x' });
    // The distributor moved: the Entrance would keep pushing to a dead endpoint otherwise.
    plugin.emit('endpoint', ROTATED);
    await flush();
    const sent = fake.requests.map((r) => `${r.method} ${r.target}`);
    expect(sent.slice(-2)).toEqual(['DELETE /v1/push/subscriptions/sub_7', 'POST /v1/push/subscriptions']);
    const post = fake.requests.at(-1)!;
    expect(JSON.parse(new TextDecoder().decode(post.body!))).toEqual({
      channel: 'web_push',
      endpoint: ROTATED.endpoint,
      keys: { p256dh: ROTATED.p256dh, auth: ROTATED.auth },
    });
    // The new id is the one logout will withdraw.
    fake.respond('DELETE /v1/push/subscriptions/sub_8', 204);
    await board.session.logout();
    expect(fake.requests.map((r) => `${r.method} ${r.target}`).at(-2)).toBe(
      'DELETE /v1/push/subscriptions/sub_8',
    );
  });

  it('forgets the subscription when the distributor drops the app', async () => {
    const plugin = new FakePlugin();
    const { fake, board } = await signedIn(plugin);
    await board.push.register();
    await flush();
    plugin.emit('unregistered');
    await flush();
    // The endpoint is dead: the Entrance's copy goes, and the card may offer push again.
    expect(fake.requests.map((r) => `${r.method} ${r.target}`).at(-1)).toBe(
      'DELETE /v1/push/subscriptions/sub_7',
    );
    expect(await board.push.isRegistered()).toBe(false);
    // Nothing more happens at logout: there is nothing left to withdraw.
    await board.session.logout();
    expect(fake.requests.map((r) => r.method).slice(-2)).toEqual(['DELETE', 'POST']);
  });
});
