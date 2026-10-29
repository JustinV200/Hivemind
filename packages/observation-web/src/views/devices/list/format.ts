/**
 * Small formatting rules for a device row: status words, the spend cap, capabilities, platform.
 *
 * Kept out of the components so the words a row shows are tested once and read the same in the
 * Devices list and in the Enrol view's pending cards.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/devices/list/`. Used by `device_row.tsx` and the Enrol
 *   view's pending list.
 *
 * Key invariants:
 *   - Pure: no clock, no I/O.
 *   - A field the Entrance leaves null is a word ("uncapped", "never"), never a blank.
 *   - Display text a device sent about itself is returned as given; React escapes it on draw.
 *
 * See Also:
 *   - docs/observation/design.md section 8 for the wording rules.
 */
import { capabilityWords as phraseOf } from '../../../components';
import type { DeviceRecord } from '../slice';

// A status as the word the page shows: the enum in lower case.
export const statusWord = (status: DeviceRecord['status']): string => status.toLowerCase();

export function spendCapWords(cap: number | null): string {
  return cap === null ? 'uncapped' : `$${cap.toFixed(2)} per day`;
}

// What a device may do, each capability as a phrase with its code, in the Entrance's order.
export const capabilityWords = (capabilities: readonly string[]): string =>
  capabilities.length === 0 ? 'nothing' : capabilities.map(phraseOf).join('; ');

// What the device said about its platform at redemption, or a word saying it did not.
export const platformWords = (device: DeviceRecord): string =>
  device.description?.platform || 'not reported';

// Splits what a person typed into a set of capability strings: spaces, commas and lines divide.
export function parseCapabilities(text: string): string[] {
  return [...new Set(text.split(/[\s,]+/).filter((one) => one !== ''))];
}
