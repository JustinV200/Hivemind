/**
 * Choose the `Browser` the app runs on: the web binding, or the Android shell's.
 *
 * ADR-0050: one `vite build` serves the page, the PWA and the APK, and the differences sit behind
 * the `platform` seam. The choice is made once, at startup, by asking Capacitor whether the app is
 * running in a native shell. Everything is passed in, so the choice is tested without Capacitor and
 * the Android half is only loaded (a dynamic import) when it is needed.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/landing_board/client/platform/`. Called by `src/main.tsx`.
 *
 * Key invariants:
 *   - Exactly one of `web` and `android` runs; the other is never called.
 *   - No code branches on the platform anywhere else; views see only the optional `Browser` members.
 *
 * See Also:
 *   - ./android/index.ts and ./real_browser.ts, the two bindings.
 */
import type { Browser } from '../browser';

export interface Choice {
  isNative: () => boolean;
  web: () => Browser;
  android: () => Promise<Browser>;
}

export function chooseBrowser(choice: Choice): Promise<Browser> {
  return choice.isNative() ? choice.android() : Promise.resolve(choice.web());
}
