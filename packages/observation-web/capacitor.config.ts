/**
 * Capacitor configuration for the Observation Hive APK (roadmap 12.12, ADR-0050).
 *
 * Capacitor wraps the output of the one `vite build` in an Android WebView. This file fixes the
 * WebView's origin and where the bundle comes from; everything else about the phone is native
 * code under `android/` or a branch of the `platform` seam in `src/landing_board/client/platform/`.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web). Read by `npx cap sync android` and `npx cap run android`; the
 *   values are copied into `android/app/src/main/assets/capacitor.config.json`. Nothing in the
 *   app imports it.
 *
 * Key invariants:
 *   - The bundle is served from `https://observation.hive.local`, one fixed origin, so the app
 *     never loads code from the network and the Entrance can accept exactly that origin beside its
 *     own `public_url` (ADR-0050). `hostname` and `androidScheme` must never vary per build.
 *   - Cleartext is off and the web view is not debuggable in a release build.
 *   - The app id is the package name the Entrance's Digital Asset Links file must list.
 *
 * See Also:
 *   - docs/adr/0050-android-client-capacitor-and-push-delivery.md.
 *   - android/README.md for how to build, sign and run.
 */
import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'dev.hivemind.observation',
  appName: 'Observation Hive',
  // The same build the Entrance serves; nothing forks per target (ADR-0049).
  webDir: 'dist',
  server: {
    androidScheme: 'https',
    hostname: 'observation.hive.local',
    cleartext: false,
  },
  android: {
    // Mixed content would let a plain-http Entrance be reached from the https origin.
    allowMixedContent: false,
    webContentsDebuggingEnabled: false,
  },
  plugins: {
    LocalNotifications: {
      // Small icon drawable the notification uses; shipped in android/app/src/main/res.
      smallIcon: 'ic_stat_hive',
    },
  },
};

export default config;
