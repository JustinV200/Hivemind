/**
 * The Hive Stand browser link: take this serve's stand key off `/stand#key=<key>` into the tab.
 *
 * The Entrance's loopback-only actions (approving, denying, unlocking, revoking a device, widening
 * what it may do, reopening the door) need the stand key in an `X-Hive-Stand` header (ADR-0068),
 * because a Virtual Cell can reach the Hive Stand's loopback too. `hive serve` prints a link with
 * the key in the fragment, which no server ever sees. On load, `adoptStandLink` keeps the key in
 * this tab's `sessionStorage` and drops it from the address bar; `heldStandKey` reads it back for
 * every request. `sessionStorage` is this origin's (scheme, host and port) and this tab's alone,
 * unlike a cookie, which a browser sends to every port on `localhost`.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/landing_board/client/platform/`. `adoptStandLink` is called by
 *   `src/main.tsx` before the app renders; `heldStandKey` backs `realBrowser().standKey`.
 *
 * Key invariants:
 *   - Only a well-formed key on the `/stand` path is kept; anything else is left alone.
 *   - Storage that throws (a locked-down browser) is treated as holding nothing.
 *
 * See Also:
 *   - docs/adr/0068-loopback-only-rows-need-the-stand-key.md for why the key exists.
 */

export const STAND_PATH = '/stand';
// The tab's own store entry for the key; never a cookie (see the header).
const STORAGE_KEY = 'hive_stand';
// 256 random bits as unpadded base64url: exactly what the Entrance mints.
const FRAGMENT = /^#key=([A-Za-z0-9_-]{43})$/;

export type StandStorage = Pick<Storage, 'getItem' | 'setItem'>;

export interface StandLocation {
  pathname: string;
  hash: string;
}

// Keeps the link's key for this tab and leaves the address at `/`; true when a key was taken.
export function adoptStandLink(
  location: StandLocation,
  storage: StandStorage,
  replace: (url: string) => void,
): boolean {
  if (location.pathname !== STAND_PATH) return false;
  const key = FRAGMENT.exec(location.hash)?.[1];
  if (!key) return false;
  try {
    storage.setItem(STORAGE_KEY, key);
  } catch {
    return false;
  }
  // Out of the address bar and the history, so a screenshot or a shared tab does not carry it.
  replace('/');
  return true;
}

// The key this tab took from the link, or null.
export function heldStandKey(storage: StandStorage): string | null {
  try {
    return storage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}
