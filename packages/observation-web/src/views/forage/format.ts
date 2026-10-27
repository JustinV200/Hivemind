/**
 * Number formatting shared by the Forage view's tables and diagrams.
 *
 * Forage figures come in three shapes: counts (seats, sub-bees, tokens), bytes of memory and US
 * dollars. One place formats each so a figure reads the same in a table, a diagram label and the
 * hidden table beside a diagram, and so the tests can name the exact text. Times are not here: a
 * grant's expiry is a `TimeStamp`, like every other moment in the views.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/forage/`. Used by every table and diagram module here.
 *
 * Key invariants:
 *   - Pure: no clock, no locale lookups beyond fixed en-US digits.
 *   - Output is plain text, never markup.
 *
 * See Also:
 *   - src/components/time/time_stamp.tsx for the relative time every view shares.
 */

const KIB = 1024;
const UNITS = ['B', 'KB', 'MB', 'GB', 'TB'] as const;

// Counts and whole figures with thousands separators, so 128000 reads as 128,000.
export const count = (n: number): string => n.toLocaleString('en-US');

// At most one decimal, dropping a trailing .0, for fractional cores and the like.
export function figure(n: number): string {
  const rounded = Math.round(n * 10) / 10;
  return rounded.toLocaleString('en-US', { maximumFractionDigits: 1 });
}

// Bytes in binary steps with one decimal below 10 and none above, e.g. `7.5 GB`, `64 GB`.
export function bytes(n: number): string {
  let value = Math.max(0, n);
  let unit = 0;
  // Climb one unit per factor of 1024 until the number is short enough to read.
  while (value >= KIB && unit < UNITS.length - 1) {
    value /= KIB;
    unit += 1;
  }
  const text = value < 10 && unit > 0 ? figure(value) : String(Math.round(value));
  return `${text} ${UNITS[unit]}`;
}

// Dollars: cents under a hundred, whole dollars above, always with the sign.
export function usd(n: number): string {
  return n < 100 ? `$${n.toFixed(2)}` : `$${Math.round(n).toLocaleString('en-US')}`;
}

// A label cut to `max` characters with an ellipsis; the caller keeps the full text in `<title>`.
export function cut(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, Math.max(0, max - 1))}…`;
}
