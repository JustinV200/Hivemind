/**
 * The ongoing notification shown while a question is waiting (`Browser.waitingNotifier`).
 *
 * ADR-0047: with no push distributor installed, the phone falls back to the live push stream while
 * the app is in the foreground and keeps an ongoing local notification up for as long as a question
 * waits, withdrawn when it is answered anywhere. This adapter posts and removes that notification
 * through `@capacitor/local-notifications`. The text says only that something is waiting; the app
 * fetches the question itself, over its own session, when the human opens it.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/landing_board/client/platform/android/`. Built by `index.ts`;
 *   driven by `src/share/` from the push stream's notices.
 *
 * Key invariants:
 *   - One notification id is reused, so a second question updates the notice rather than stacking.
 *   - Notification permission is asked for once, on the first show; a refusal is silent, because
 *     the question is still in the chat and the notification is only a nudge.
 *   - The notification is `ongoing`, so only the app (or the answer) removes it.
 *
 * See Also:
 *   - capacitor.config.ts for the small icon, and docs/entrance/landing-board.md section 9.
 */
import type { LocalNotificationsPlugin } from '@capacitor/local-notifications';
import type { WaitingNotifier } from '../../browser';

// The slice of the plugin used here, so a test supplies a small fake.
export type NotificationsLike = Pick<
  LocalNotificationsPlugin,
  'schedule' | 'cancel' | 'checkPermissions' | 'requestPermissions'
>;

const NOTICE_ID = 4102;

export function androidWaitingNotifier(plugin: NotificationsLike): WaitingNotifier {
  async function allowed(): Promise<boolean> {
    const state = await plugin.checkPermissions();
    if (state.display === 'granted') return true;
    return (await plugin.requestPermissions()).display === 'granted';
  }
  return {
    async show(count) {
      if (!(await allowed())) return;
      await plugin.schedule({
        notifications: [
          {
            id: NOTICE_ID,
            title: 'Observation Hive',
            body: count === 1 ? 'A question is waiting.' : `${count} questions are waiting.`,
            ongoing: true,
            autoCancel: false,
          },
        ],
      });
    },
    async clear() {
      await plugin.cancel({ notifications: [{ id: NOTICE_ID }] });
    },
  };
}
