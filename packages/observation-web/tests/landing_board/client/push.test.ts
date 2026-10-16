/**
 * Tests for Web Push registration.
 *
 * Registration reads the Hive's application key, asks the browser to subscribe under it, and
 * posts the endpoint and keys as a `web_push` subscription. The key must reach the browser as
 * bytes, and the post must be a signed request.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/landing_board/client/push.ts.
 *
 * Key invariants:
 *   - The body posted is the generated `SubscribeBody` shape.
 *
 * See Also:
 *   - docs/entrance/landing-board.md section 8.2 (Web Push).
 */
import { describe, expect, it } from 'vitest';
import { createLandingBoard } from '../../../src/landing_board/client';
import type { PushChange } from '../../../src/landing_board/client';
import { base64urlEncode } from '../../../src/landing_board/client/signing';
import { createFakeBrowser, flush } from '../../support/fake_browser';
import { storeSession } from '../../support/session_fixture';

describe('push registration', () => {
  it('fetches the key, subscribes under it and registers the subscription', async () => {
    const fake = createFakeBrowser();
    await storeSession(fake.browser);
    const received: Uint8Array[] = [];
    const original = fake.browser.subscribeWebPush;
    fake.browser.subscribeWebPush = (key) => {
      received.push(key);
      return original(key);
    };
    const board = createLandingBoard({ browser: fake.browser, onSessionLost: () => {} });
    await board.session.restore();
    fake.respond('GET /v1/push/vapid-key', 200, {
      application_server_key: base64urlEncode(Uint8Array.from([1, 2, 3])),
    });
    fake.respond('POST /v1/push/subscriptions', 201, {
      id: 'sub_1',
      channel: 'web_push',
      created_at: 'x',
    });
    const view = await board.push.register();
    expect(view.id).toBe('sub_1');
    expect([...received[0]!]).toEqual([1, 2, 3]);
    const post = fake.requests.at(-1)!;
    expect(post.headers.Authorization).toBeDefined();
    expect(JSON.parse(new TextDecoder().decode(post.body!))).toEqual({
      channel: 'web_push',
      endpoint: 'https://push.test/abc',
      keys: { p256dh: 'P', auth: 'A' },
    });
  });

  it('serialises a rotation reported during a registration, so neither is lost', async () => {
    const fake = createFakeBrowser();
    await storeSession(fake.browser);
    let report: ((change: PushChange) => void) | null = null;
    fake.browser.pushChanges = {
      onChange: (listener) => {
        report = listener;
        return () => undefined;
      },
    };
    const board = createLandingBoard({ browser: fake.browser, onSessionLost: () => {} });
    await board.session.restore();
    fake.respond('GET /v1/push/vapid-key', 200, {
      application_server_key: base64urlEncode(Uint8Array.from([1])),
    });
    let ids = 0;
    fake.routes.set('POST /v1/push/subscriptions', () => ({
      status: 201,
      body: { id: `sub_${++ids}`, channel: 'web_push', created_at: 'x' },
    }));
    fake.respond('DELETE /v1/push/subscriptions/sub_1', 204);
    // The distributor rotates while the first registration is still being posted.
    const registering = board.push.register();
    report!({
      kind: 'endpoint',
      subscription: { endpoint: 'https://push.test/new', keys: { p256dh: 'N', auth: 'N' } },
    });
    await registering;
    await flush();
    const sent = fake.requests.map((r) => `${r.method} ${r.target}`);
    expect(sent.filter((line) => line.includes('/v1/push/subscriptions'))).toEqual([
      'POST /v1/push/subscriptions',
      'DELETE /v1/push/subscriptions/sub_1',
      'POST /v1/push/subscriptions',
    ]);
    expect(JSON.parse(new TextDecoder().decode(fake.requests.at(-1)!.body!))).toMatchObject({
      endpoint: 'https://push.test/new',
    });
    fake.respond('DELETE /v1/push/subscriptions/sub_2', 204);
    await board.push.withdrawStored();
    expect(fake.requests.at(-1)!.target).toBe('/v1/push/subscriptions/sub_2');
  });

  it('removes a subscription by id', async () => {
    const fake = createFakeBrowser();
    await storeSession(fake.browser);
    const board = createLandingBoard({ browser: fake.browser, onSessionLost: () => {} });
    await board.session.restore();
    fake.respond('DELETE /v1/push/subscriptions/sub_1', 204);
    await board.push.unregister('sub_1');
    expect(fake.requests.at(-1)).toMatchObject({
      method: 'DELETE',
      target: '/v1/push/subscriptions/sub_1',
    });
  });
});
