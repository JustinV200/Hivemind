/**
 * Money and token formatting shared by the Cost view's strip and tables.
 *
 * Spend is mostly fractions of a cent, so a dollar figure under one dollar shows four decimals and
 * from one dollar up it shows two (with thousands separators), as design.md section 5.8 asks. One
 * place formats each figure so the totals strip, the tables and the tests all name the same text.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cost/`. Used by every table and the totals strip.
 *
 * Key invariants:
 *   - Pure: fixed en-US digits, no locale lookups, no clock.
 *   - Output is plain text, never markup.
 *
 * See Also:
 *   - src/views/forage/format.ts for the Forage view's coarser dollar figure.
 */

// Dollars: four decimals under a dollar so a cheap call is visible, two from a dollar up.
export function usd(n: number): string {
  if (n < 1) return `$${n.toFixed(4)}`;
  return `$${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

// Token and call counts with thousands separators, so 128000 reads as 128,000.
export const count = (n: number): string => n.toLocaleString('en-US');

// A share of a whole as a whole percent, 0 when the whole is empty (never NaN).
export function sharePercent(part: number, whole: number): number {
  return whole > 0 ? Math.min(100, Math.round((part / whole) * 100)) : 0;
}
