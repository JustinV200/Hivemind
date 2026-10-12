/**
 * Tests for the platform choice.
 *
 * ADR-0047: one build, one choice at startup. Inside the APK the Android binding is built and the
 * web binding is never touched; in a browser it is the other way round.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises platform/choose.ts.
 *
 * Key invariants:
 *   - Exactly one binding is constructed.
 *
 * See Also:
 *   - src/main.tsx, the one caller.
 */
import { describe, expect, it } from 'vitest';
import { chooseBrowser } from '../../../../../src/landing_board/client';
import { createFakeBrowser } from '../../../../support/fake_browser';

function choice(isNative: boolean) {
  const calls: string[] = [];
  const web = createFakeBrowser().browser;
  const android = createFakeBrowser().browser;
  return {
    calls,
    web,
    android,
    options: {
      isNative: () => isNative,
      web: () => {
        calls.push('web');
        return web;
      },
      android: () => {
        calls.push('android');
        return Promise.resolve(android);
      },
    },
  };
}

describe('chooseBrowser', () => {
  it('builds only the Android binding in the APK', async () => {
    const c = choice(true);
    expect(await chooseBrowser(c.options)).toBe(c.android);
    expect(c.calls).toEqual(['android']);
  });

  it('builds only the web binding in a browser', async () => {
    const c = choice(false);
    expect(await chooseBrowser(c.options)).toBe(c.web);
    expect(c.calls).toEqual(['web']);
  });
});
