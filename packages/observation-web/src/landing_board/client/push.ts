/**
 * Web Push registration: fetch the Hive's application key, subscribe, tell the Entrance.
 *
 * The Entrance pushes content-free notices ("a question is waiting") to a phone or browser that
 * has registered a Web Push subscription. Registration is three steps: read the VAPID
 * `application_server_key`, ask the browser to subscribe under it (which prompts the human for
 * permission, so this must run from a click), and post the resulting endpoint and keys. The
 * notice itself never carries content (landing-board.md section 9), so there is nothing here to
 * decrypt or store.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/landing_board/client/`. Built by `index.ts`; called by the
 *   Devices view when a person turns notifications on (a later step wires the button).
 *
 * Key invariants:
 *   - Every request shape is the generated one (`SubscribeBody`, `VapidKeyView`).
 *   - The subscription id is kept so logout can withdraw it; a platform that owns a distributor
 *     registration (the Android shell) is told to drop it too, even when the Entrance is down.
 *   - The application key is decoded from unpadded base64url before the browser sees it.
 *
 * See Also:
 *   - docs/entrance/landing-board.md section 8.2 (Web Push) and section 9 (the push contract).
 */
import type { components } from '../types';
import type { Browser } from './browser';
import type { HttpClient } from './http';
import { clearPushSubscription, loadPushSubscription, savePushSubscription } from './keys';
import { base64urlDecode } from './signing';

export type SubscriptionView = components['schemas']['SubscriptionView'];

export interface PushClient {
  register(): Promise<SubscriptionView>;
  unregister(subscriptionId: string): Promise<void>;
  // True when this device already registered a subscription (so a prompt to turn push on is moot).
  isRegistered(): Promise<boolean>;
  // Logout: withdraws the subscription this device registered, if it registered one. It never
  // throws, because leaving must not wait on an Entrance that cannot be reached.
  withdrawStored(): Promise<void>;
}

export function createPush(browser: Browser, http: HttpClient): PushClient {
  // Deletes the Entrance's copy, then the platform's own registration, then the remembered id.
  async function unregister(subscriptionId: string): Promise<void> {
    await http.call('delete', '/v1/push/subscriptions/{subscription_id}', {
      params: { subscription_id: subscriptionId },
    });
    await browser.unsubscribeWebPush?.();
    await clearPushSubscription(browser.store);
  }
  return {
    async register() {
      const key = await http.call('get', '/v1/push/vapid-key');
      const subscription = await browser.subscribeWebPush(
        base64urlDecode(key.application_server_key),
      );
      const view = await http.call('post', '/v1/push/subscriptions', {
        body: {
          channel: 'web_push',
          endpoint: subscription.endpoint,
          keys: subscription.keys,
        },
      });
      await savePushSubscription(browser.store, view.id);
      return view;
    },
    unregister,
    isRegistered: async () => (await loadPushSubscription(browser.store)) !== null,
    async withdrawStored() {
      const id = await loadPushSubscription(browser.store);
      if (id === null) return;
      try {
        await unregister(id);
      } catch {
        // The Entrance may be unreachable; the local registration still has to go.
        await browser.unsubscribeWebPush?.();
        await clearPushSubscription(browser.store);
      }
    },
  };
}
