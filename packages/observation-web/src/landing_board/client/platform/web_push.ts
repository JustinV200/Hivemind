/**
 * Web Push subscription through the page's service worker.
 *
 * Push needs three browser steps in order: the human grants notification permission (which must
 * come from a click), the service worker registration is ready, and `PushManager.subscribe` runs
 * under the Hive's application server key. The Entrance later sends content-free notices to the
 * resulting endpoint. This is the "push registration" half of ADR-0049's platform seam; the
 * Capacitor APK will supply its own implementation of the same `Browser` member.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/landing_board/client/platform/`. Used by `real_browser.ts` as
 *   `Browser.subscribeWebPush`; called from `push.ts`.
 *
 * Key invariants:
 *   - A refusal of permission, or a browser with no service worker or push support, is an error
 *     the caller shows; nothing is registered in that case.
 *   - Subscriptions are `userVisibleOnly`, which is the only mode browsers accept.
 *
 * See Also:
 *   - docs/entrance/landing-board.md section 8.2 for Web Push.
 */
import type { Bytes, WebPushSubscription } from '../browser';
import { base64urlEncode } from '../signing';

function keyOf(subscription: PushSubscription, name: PushEncryptionKeyName): string {
  const raw = subscription.getKey(name);
  if (!raw) throw new Error('The push subscription has no key.');
  return base64urlEncode(new Uint8Array(raw));
}

export async function subscribeWebPush(serverKey: Bytes): Promise<WebPushSubscription> {
  if (!('serviceWorker' in navigator) || typeof Notification === 'undefined') {
    throw new Error('This browser does not support Web Push.');
  }
  if ((await Notification.requestPermission()) !== 'granted') {
    throw new Error('Notification permission was not granted.');
  }
  const registration = await navigator.serviceWorker.ready;
  const subscription = await registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: serverKey,
  });
  return {
    endpoint: subscription.endpoint,
    keys: { p256dh: keyOf(subscription, 'p256dh'), auth: keyOf(subscription, 'auth') },
  };
}
