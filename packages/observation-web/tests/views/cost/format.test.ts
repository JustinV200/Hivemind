/**
 * Tests for the Cost view's formatting: dollars and counts.
 *
 * These pin the rule that a figure shows two decimals with thousands separators, and four only
 * below one cent so a fraction of a cent is not shown as nothing, and that counts get thousands
 * separators.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/cost/format.ts.
 *
 * Key invariants:
 *   - The text is exact, because the tables and the strip show it verbatim.
 *
 * See Also:
 *   - tests/views/cost/cost_view.test.tsx for the figures as drawn.
 */
import { describe, expect, it } from 'vitest';
import { count, sharePercent, usd } from '../../../src/views/cost/format';

describe('usd', () => {
  it('shows two decimals with thousands separators, zero included', () => {
    expect(usd(0)).toBe('$0.00');
    expect(usd(0.0123)).toBe('$0.01');
    expect(usd(0.5)).toBe('$0.50');
    expect(usd(12.345)).toBe('$12.35');
    expect(usd(1234567.891)).toBe('$1,234,567.89');
  });

  it('shows four decimals only below one cent', () => {
    expect(usd(0.005)).toBe('$0.0050');
    expect(usd(0.00004)).toBe('$0.0000');
  });
});

describe('count', () => {
  it('puts thousands separators in token counts', () => {
    expect(count(0)).toBe('0');
    expect(count(999)).toBe('999');
    expect(count(128000)).toBe('128,000');
    expect(count(12345678)).toBe('12,345,678');
  });
});

describe('sharePercent', () => {
  it('is a whole percent and 0 for an empty whole', () => {
    expect(sharePercent(1, 4)).toBe(25);
    expect(sharePercent(0, 0)).toBe(0);
    expect(sharePercent(3, 3)).toBe(100);
  });
});
