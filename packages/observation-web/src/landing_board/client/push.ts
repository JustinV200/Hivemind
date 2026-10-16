/**
 * Web Push registration: fetch the Hive's application key, subscribe, tell the Entrance.
 *
 * The Entrance pushes content-free notices ("a question is waiting") to a phone or browser that
 * has registered a Web Push subscription. Registration is three steps: read the VAPID
 * `application_server_key`, ask the browser to subscribe under it (which prompts the human for
 * permission, so this must run from a click), and post the resulting endpoint and keys. The
 * notice itself never carries content (landing-board.md section 9), so there is nothing here to
 * decrypt or store. A platform that owns a distributor registration (the Android shell) can also
 * report changes the distributor made on its own: a rotated endpoint is registered afresh, with
 * the old subscription deleted first, and a dropped registration withdraws the subscription, so
 * the Entrance never keeps pushing to a dead endpoint while the device believes it is registered.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/landing_board/client/`. Built by `index.ts`; called by the
 *   push card when a person turns notifications on, and by logout.
 *
 * Key invariants:
 *   - Every request shape is the generated one (`SubscribeBody`, `VapidKeyView`).
 *   - The subscription id is kept so logout can withdraw it; a platform that owns a distributor
 *     registration (the Android shell) is told to drop it too, even when the Entrance is down.
 *   - The application key is decoded from unpadded base64url before the browser sees it.
 *   - Registrations, withdrawals and the platform's changes run one after another, never
 *     interleaved, so a rotation reported mid-registration cannot leave two subscriptions or lose
 *     the id of the one that stands.
 *
 * See Also:
 *   - docs/entrance/landing-board.md section 8.2 (Web Push) and section 9 (the push contract).
 */
import type { components } from '../types';
import type { Browser, PushChange, WebPushSubscription } from './browser';
import type { HttpClient } from './http';
import { clearPushSubscription, loadPushSubscription, savePushSubscription } from './keys';
import { base64urlDecode } from './signing';

export type SubscriptionView = components['schemas']['SubscriptionView'];

// One lane for every push operation: each waits for the one before it, failed or not.
function lane(): <T>(work: () => Promise<T>) => Promise<T> {
  let last: Promise<unknown> = Promise.resolve();
  return (work) => {
    const result = last.then(work);
    last = result.catch(() => undefined);
    return result;
  };
}

export interface PushClient {
  register(): Promise<SubscriptionView>;
  unregister(subscriptionId: string): Promise<void>;
  // True when this device already registered a subscription (so a prompt to turn push on is moot).
  isRegistered(): Promise<boolean>;
  // Logout: withdraws the subscription this device registered, if it registered one. It never
  // throws, because leaving must not wait on an Entrance that cannot be reached.
  withdrawStored(): Promise<void>;
}

interface PushDeps {
  browser: Browser;
  http: HttpClient;
}

// Posts one subscription and remembers its id, so logout (or a rotation) can withdraw it.
async function subscribe(
  deps: PushDeps,
  subscription: WebPushSubscription,
): Promise<SubscriptionView> {
  const view = await deps.http.call('post', '/v1/push/subscriptions', {
    body: { channel: 'web_push', endpoint: subscription.endpoint, keys: subscription.keys },
  });
  await savePushSubscription(deps.browser.store, view.id);
  return view;
}

async function register(deps: PushDeps): Promise<SubscriptionView> {
  const key = await deps.http.call('get', '/v1/push/vapid-key');
  const subscription = await deps.browser.subscribeWebPush(
    base64urlDecode(key.application_server_key),
  );
  return subscribe(deps, subscription);
}

function deleteOnEntrance(deps: PushDeps, subscriptionId: string): Promise<void> {
  return deps.http.call('delete', '/v1/push/subscriptions/{subscription_id}', {
    params: { subscription_id: subscriptionId },
  });
}

// Deletes the Entrance's copy, then the platform's own registration, then the remembered id.
async function unregister(deps: PushDeps, subscriptionId: string): Promise<void> {
  await deleteOnEntrance(deps, subscriptionId);
  await deps.browser.unsubscribeWebPush?.();
  await clearPushSubscription(deps.browser.store);
}

async function withdrawStored(deps: PushDeps): Promise<void> {
  const id = await loadPushSubscription(deps.browser.store);
  if (id === null) return;
  try {
    await unregister(deps, id);
  } catch {
    // The Entrance may be unreachable; the local registration still has to go.
    await deps.browser.unsubscribeWebPush?.();
    await clearPushSubscription(deps.browser.store);
  }
}

// The distributor spoke on its own: the Entrance must hold its current endpoint, or none. The
// stored subscription names a dead endpoint either way, so a refusal of its deletion (the
// Entrance already dropped it, or is unreachable) changes nothing.
async function applyChange(deps: PushDeps, change: PushChange): Promise<void> {
  const id = await loadPushSubscription(deps.browser.store);
  if (id !== null) {
    await deleteOnEntrance(deps, id).catch(() => undefined);
    await clearPushSubscription(deps.browser.store);
  }
  if (change.kind === 'endpoint') await subscribe(deps, change.subscription);
}

export function createPush(browser: Browser, http: HttpClient): PushClient {
  const deps: PushDeps = { browser, http };
  const queued = lane();
  browser.pushChanges?.onChange((change) => {
    // With no session (the device was logged out by a 401), the post fails and the change is
    // lost; the next login's push card then offers registration again.
    void queued(() => applyChange(deps, change)).catch(() => undefined);
  });
  return {
    register: () => queued(() => register(deps)),
    unregister: (subscriptionId) => queued(() => unregister(deps, subscriptionId)),
    isRegistered: async () => (await loadPushSubscription(browser.store)) !== null,
    withdrawStored: () => queued(() => withdrawStored(deps)),
  };
}
