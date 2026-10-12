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
 *   - The application key is decoded from unpadded base64url before the browser sees it.
 *
 * See Also:
 *   - docs/entrance/landing-board.md section 8.2 (Web Push) and section 9 (the push contract).
 */
import type { components } from '../types';
import type { Browser } from './browser';
import type { HttpClient } from './http';
import { base64urlDecode } from './signing';

export type SubscriptionView = components['schemas']['SubscriptionView'];

export interface PushClient {
  register(): Promise<SubscriptionView>;
  unregister(subscriptionId: string): Promise<void>;
}

export function createPush(browser: Browser, http: HttpClient): PushClient {
  return {
    async register() {
      const key = await http.call('get', '/v1/push/vapid-key');
      const subscription = await browser.subscribeWebPush(
        base64urlDecode(key.application_server_key),
      );
      return http.call('post', '/v1/push/subscriptions', {
        body: {
          channel: 'web_push',
          endpoint: subscription.endpoint,
          keys: subscription.keys,
        },
      });
    },
    async unregister(subscriptionId) {
      await http.call('delete', '/v1/push/subscriptions/{subscription_id}', {
        params: { subscription_id: subscriptionId },
      });
    },
  };
}
