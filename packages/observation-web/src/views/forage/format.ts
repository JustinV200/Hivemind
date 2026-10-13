/**
 * Number and time formatting shared by the Forage view's tables and diagrams.
 *
 * Forage figures come in four shapes: counts (seats, sub-bees, tokens), bytes of memory, US dollars
 * and the minutes left on a lease. One place formats each so a figure reads the same in a table, a
 * diagram label and the hidden table beside a diagram, and so the tests can name the exact text.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/forage/`. Used by every table and diagram module here.
 *
 * Key invariants:
 *   - Pure: no clock (the caller passes `nowMs`), no locale lookups beyond fixed en-US digits.
 *   - Output is plain text, never markup.
 *
 * See Also:
 *   - src/views/forage/tables/expiry.ts for the ten-minute rule that uses `minutesLeft`.
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

// Whole minutes until an ISO time, rounded up; negative once it has passed; NaN reads as 0.
export function minutesLeft(iso: string, nowMs: number): number {
  const at = Date.parse(iso);
  if (Number.isNaN(at)) return 0;
  return Math.ceil((at - nowMs) / 60_000);
}

// `in 4 min`, `in 1 h 5 min`, or `lapsed` once the lease has run out.
export function leaseText(iso: string, nowMs: number): string {
  const minutes = minutesLeft(iso, nowMs);
  if (minutes <= 0) return 'lapsed';
  if (minutes < 60) return `in ${minutes} min`;
  return `in ${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

// A label cut to `max` characters with an ellipsis; the caller keeps the full text in `<title>`.
export function cut(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, Math.max(0, max - 1))}…`;
}
