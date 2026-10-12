/**
 * What this browser says about itself when it redeems an invite.
 *
 * The Entrance shows the operator a name, a platform and a user agent at approval so they can tell
 * which device is asking. All three are display text only: the Entrance treats them as unverified
 * and bounded, and so does the page that later draws them (escaped). The name is what the human
 * typed; the platform and user agent come from the browser and are cut to the document's limits.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/enrol/join/`. Called by `use_join.ts` at redemption.
 *
 * Key invariants:
 *   - Lengths never exceed the document's limits (64, 64 and 512 characters).
 *   - Nothing here is a secret or an identifier that outlives the page.
 *
 * See Also:
 *   - docs/entrance/openapi.json, `DeviceDescription`.
 */
import type { DeviceDescription } from '../../../landing_board/client';

const NAME_MAX = 64;
const PLATFORM_MAX = 64;
const AGENT_MAX = 512;

// `userAgentData` is the modern source; `platform` is the older, deprecated fallback.
interface NavigatorWithHints extends Navigator {
  userAgentData?: { platform?: string };
}

export function describeThisDevice(name: string): DeviceDescription {
  const nav: NavigatorWithHints = navigator;
  const platform = nav.userAgentData?.platform ?? nav.platform ?? '';
  return {
    name: name.trim().slice(0, NAME_MAX),
    platform: platform.slice(0, PLATFORM_MAX),
    user_agent: (nav.userAgent ?? '').slice(0, AGENT_MAX),
  };
}
