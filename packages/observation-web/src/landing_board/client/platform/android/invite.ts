/**
 * Invite links on Android: learn the Entrance's address from `<base>/enrol#code=<code>`.
 *
 * A browser tab takes the Entrance's origin from its own address bar. The APK's page lives on a
 * pinned local origin, so the Entrance's address has to come from somewhere else: the App Link the
 * operator's invite opens (QR enrolment, landing-board.md section 4), or the same link pasted by
 * hand when the link does not verify. Adopting a link stores its origin beside the session
 * (`keys.ts`) and returns the code so the join screen, which reads the code from the address,
 * runs unchanged.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/landing_board/client/platform/android/`. Used by `index.ts`
 *   for the launch URL, for `appUrlOpen` and for `InviteLinks.accept`.
 *
 * Key invariants:
 *   - Only `https` links whose path is exactly `/enrol` and which carry a code of the shape the
 *     Entrance mints are accepted; anything else is `null`.
 *   - A device that already holds a session never changes Entrance because of a link: the session's
 *     bearer token must not be steered at another host by an intent from another app.
 *   - The code is returned to the caller and never stored or logged; the base is not a secret.
 *
 * See Also:
 *   - src/views/enrol/join/invite_code.ts for the web side that reads the code from the address.
 *   - docs/adr/0047-android-client-capacitor-and-push-delivery.md, "The phone is an enrolled device".
 */
import type { KeyValueStore } from '../../browser';
import { loadSession, saveBaseUrl } from '../../keys';

export interface InviteTarget {
  baseUrl: string;
  code: string;
}

// Letters, digits and dashes, as the join screen accepts (the Entrance's codes are 26 characters).
const CODE = /[#?&]code=([A-Za-z0-9-]{8,64})(?:&|$)/;

export function parseInviteLink(text: string): InviteTarget | null {
  let url: URL;
  try {
    url = new URL(text.trim());
  } catch {
    return null;
  }
  if (url.protocol !== 'https:' || url.username !== '' || url.password !== '') return null;
  if (url.pathname.replace(/\/$/, '') !== '/enrol') return null;
  const code = CODE.exec(url.hash)?.[1];
  return code ? { baseUrl: url.origin, code } : null;
}

// Stores the link's origin and returns its code, or null when the link is not for this device.
export async function adoptInvite(store: KeyValueStore, link: string): Promise<string | null> {
  const target = parseInviteLink(link);
  if (!target) return null;
  if ((await loadSession(store)) !== null) return null;
  await saveBaseUrl(store, target.baseUrl);
  return target.code;
}

// The address the join screen reads its code from.
export function joinHash(code: string): string {
  return `#code=${code}`;
}
