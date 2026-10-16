/**
 * Push on Android: a UnifiedPush distributor behind `Browser.subscribeWebPush`.
 *
 * The page's `PushManager` does not exist in a bundled WebView, and ADR-0050 chooses UnifiedPush
 * over FCM. The Entrance's `client/push.ts` reads the Hive's VAPID key and asks the browser to
 * subscribe under it; this adapter answers that same question from the native plugin: the
 * distributor registers under the key and returns an endpoint that speaks Web Push together with
 * the `p256dh` and `auth` keys. The result has the shape of a browser subscription, so the existing
 * `POST /v1/push/subscriptions` call with `channel: web_push` registers it unchanged. A distributor
 * can also move on its own (a server move, a re-registration after an update) or drop the app;
 * the plugin raises those as `endpoint` and `unregistered` events, which this adapter relays as
 * `Browser.pushChanges` so the client registers the new endpoint or withdraws the dead one.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/landing_board/client/platform/android/`. Built by `index.ts`;
 *   used as `Browser.subscribeWebPush`, `Browser.unsubscribeWebPush` and `Browser.pushChanges`.
 *
 * Key invariants:
 *   - The key goes to the distributor as unpadded base64url, the form the connector takes.
 *   - A refusal (no distributor, permission denied, no encryption keys) rejects with a sentence the
 *     human can act on; nothing is registered in that case.
 *   - A push message is never read for content on this side either: there is none.
 *   - Listening is removable: the returned function stops delivery, even if the plugin's own
 *     listener handles have not arrived yet.
 *
 * See Also:
 *   - src/landing_board/client/push.ts, the caller, and platform/web_push.ts, the browser twin.
 *   - android/.../push/ for the native side.
 */
import type { PluginListenerHandle } from '@capacitor/core';
import type { Bytes, PushChange, PushChanges, WebPushSubscription } from '../../browser';
import { base64urlEncode } from '../../signing';
import type { PushRegistration, UnifiedPushPlugin } from './plugins';

// What each rejection code means to the human, in the README's words.
const SENTENCES: Readonly<Record<string, string>> = {
  NO_DISTRIBUTOR:
    'No UnifiedPush distributor is installed. Install one, such as ntfy, then try again.',
  DENIED: 'Notification permission was not granted.',
  UNENCRYPTED: 'That distributor does not support encrypted Web Push, which the Hive requires.',
  TIMEOUT: 'The distributor did not answer. Open it once and try again.',
};

function sentenceFor(error: unknown): string {
  const code = (error as { code?: unknown } | null)?.code;
  return (typeof code === 'string' ? SENTENCES[code] : undefined) ?? 'Push could not be set up.';
}

export interface AndroidPush {
  subscribeWebPush(serverKey: Bytes): Promise<WebPushSubscription>;
  unsubscribeWebPush(): Promise<void>;
  pushChanges: PushChanges;
}

function subscriptionOf(registration: PushRegistration): WebPushSubscription {
  return {
    endpoint: registration.endpoint,
    keys: { p256dh: registration.p256dh, auth: registration.auth },
  };
}

// Relays the plugin's two events as one stream of changes; stopping removes both listeners.
function changesOf(plugin: UnifiedPushPlugin): PushChanges {
  return {
    onChange(listener) {
      const handles: PluginListenerHandle[] = [];
      let stopped = false;
      const keep = (made: PluginListenerHandle): void => {
        // A stop that came before the handle did must still end the subscription.
        if (stopped) void made.remove();
        else handles.push(made);
      };
      const deliver = (change: PushChange): void => {
        if (!stopped) listener(change);
      };
      void plugin
        .addListener('endpoint', (registration) =>
          deliver({ kind: 'endpoint', subscription: subscriptionOf(registration) }),
        )
        .then(keep);
      void plugin.addListener('unregistered', () => deliver({ kind: 'unregistered' })).then(keep);
      return () => {
        stopped = true;
        for (const handle of handles.splice(0)) void handle.remove();
      };
    },
  };
}

export function androidPush(plugin: UnifiedPushPlugin): AndroidPush {
  return {
    pushChanges: changesOf(plugin),
    async subscribeWebPush(serverKey) {
      try {
        const registration = await plugin.register({ vapidKey: base64urlEncode(serverKey) });
        return subscriptionOf(registration);
      } catch (error) {
        throw new Error(sentenceFor(error));
      }
    },
    async unsubscribeWebPush() {
      // Best effort: the Entrance's copy is already gone, and the next register starts afresh.
      await plugin.unregister().catch(() => undefined);
    },
  };
}
