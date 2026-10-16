/**
 * The Android `Browser`: the web binding with the phone's members swapped in.
 *
 * ADR-0050 keeps one build for the page, the PWA and the APK. In the APK the same `Browser` is built
 * from `realBrowser` with six differences: the origin is the Entrance's stored address (the page's
 * own origin is a pinned local one), passkeys and push go through native plugins (push including
 * the distributor's own changes), and the share sheet, the waiting notification and invite links
 * are offered as optional members that a browser does not have. Recording stays the WebView's
 * `MediaRecorder`. Nothing above the seam changes.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/landing_board/client/platform/android/`. Chosen at startup by
 *   `../choose.ts` when Capacitor reports a native platform; loaded lazily so the web bundle does
 *   not carry it.
 *
 * Key invariants:
 *   - The origin is the stored base URL when there is one, else the page's own origin (which reaches
 *     nothing, so the login screen's request fails plainly until an invite link is opened).
 *   - An invite link delivered at launch is adopted before the app mounts; one delivered while the
 *     app runs reloads the page on its join screen, so every module reads the new origin afresh.
 *   - Every Capacitor object is injected, so the whole function is tested without a device.
 *
 * See Also:
 *   - ../real_browser.ts for the web binding, ../choose.ts for the choice, ./invite.ts for links.
 */
import type { AppPlugin } from '@capacitor/app';
import { App } from '@capacitor/app';
import { LocalNotifications } from '@capacitor/local-notifications';
import type { Browser, KeyValueStore } from '../../browser';
import { loadBaseUrl } from '../../keys';
import { indexedDbStore } from '../indexed_db';
import { realBrowser } from '../real_browser';
import { adoptInvite, joinHash } from './invite';
import { androidPasskeys } from './passkey';
import { realPlugins } from './plugins';
import type { NativePlugins } from './plugins';
import { androidPush } from './push';
import { androidShareIntake } from './share';
import { androidWaitingNotifier } from './waiting';
import type { NotificationsLike } from './waiting';

export interface AndroidDeps {
  plugins: NativePlugins;
  app: Pick<AppPlugin, 'getLaunchUrl' | 'addListener'>;
  notifications: NotificationsLike;
  store: KeyValueStore;
  // The web binding to start from; the Android members are laid over what it returns.
  web: (overrides: Partial<Browser>) => Browser;
  // The page's own origin, used while no Entrance address is stored.
  pageOrigin: string;
  // Points the address bar at the join screen; `reload` is true when the app is already running.
  showJoin: (code: string, reload: boolean) => void;
}

export function realAndroidDeps(): AndroidDeps {
  return {
    plugins: realPlugins(),
    app: App,
    notifications: LocalNotifications,
    store: indexedDbStore(window.indexedDB),
    web: (overrides) => realBrowser(overrides),
    pageOrigin: window.location.origin,
    showJoin: (code, reload) => {
      if (reload) {
        window.location.hash = joinHash(code);
        window.location.reload();
      } else {
        window.history.replaceState(null, '', window.location.pathname + joinHash(code));
      }
    },
  };
}

export async function androidBrowser(deps: AndroidDeps = realAndroidDeps()): Promise<Browser> {
  const { store } = deps;
  // The app was started by an invite link: adopt it now so the first render shows the join screen.
  const launch = await deps.app.getLaunchUrl();
  const launched = launch ? await adoptInvite(store, launch.url) : null;
  if (launched) deps.showJoin(launched, false);
  // A link opened while the app runs is adopted and the page reloads onto the join screen.
  void deps.app.addListener('appUrlOpen', (event) => {
    void adoptInvite(store, event.url).then((code) => {
      if (code) deps.showJoin(code, true);
    });
  });

  const base = await loadBaseUrl(store);
  const passkeys = androidPasskeys(deps.plugins.passkey);
  const push = androidPush(deps.plugins.push);
  return deps.web({
    store,
    origin: base ?? deps.pageOrigin,
    assertPasskey: passkeys.assertPasskey,
    createPasskey: passkeys.createPasskey,
    subscribeWebPush: push.subscribeWebPush,
    unsubscribeWebPush: push.unsubscribeWebPush,
    pushChanges: push.pushChanges,
    shareIntake: androidShareIntake(deps.plugins.share),
    waitingNotifier: androidWaitingNotifier(deps.notifications),
    inviteLinks: {
      configured: base !== null,
      accept: async (link) => {
        const code = await adoptInvite(store, link);
        if (code) deps.showJoin(code, true);
        return code !== null;
      },
    },
  });
}
