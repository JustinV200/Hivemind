/**
 * Tests for the Hive Stand browser link: the stand key moves from the fragment into the tab.
 *
 * `hive serve` prints `/stand#key=<key>` (ADR-0068). The app keeps a well-formed key in this tab's
 * `sessionStorage`, puts the address back to `/`, and leaves every other link alone.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises platform/stand_link.ts.
 *
 * Key invariants:
 *   - Only `/stand` with a 43-character base64url key is adopted.
 *
 * See Also:
 *   - docs/adr/0068-loopback-only-rows-need-the-stand-key.md.
 */
import { describe, expect, it } from 'vitest';
import {
  adoptStandLink,
  heldStandKey,
} from '../../../../src/landing_board/client/platform/stand_link';
import type { StandStorage } from '../../../../src/landing_board/client/platform/stand_link';

const KEY = 'A'.repeat(42) + '_';

function memory(): StandStorage {
  const values = new Map<string, string>();
  return {
    getItem: (name) => values.get(name) ?? null,
    setItem: (name, value) => void values.set(name, value),
  };
}

describe('the stand link', () => {
  it.each([
    ['/stand', `#key=${KEY}`, KEY],
    ['/enrol', `#key=${KEY}`, null],
    ['/stand', '#key=short', null],
    ['/stand', `#key=${KEY}&more`, null],
  ])('on %s%s keeps %s', (pathname, hash, kept) => {
    const storage = memory();
    const replaced: string[] = [];
    adoptStandLink({ pathname, hash }, storage, (url) => replaced.push(url));
    expect(heldStandKey(storage)).toBe(kept);
    expect(replaced).toEqual(kept ? ['/'] : []);
  });

  it('treats storage that throws as holding nothing', () => {
    const broken: StandStorage = {
      getItem: () => {
        throw new Error('blocked');
      },
      setItem: () => {
        throw new Error('blocked');
      },
    };
    expect(adoptStandLink({ pathname: '/stand', hash: `#key=${KEY}` }, broken, () => {})).toBe(
      false,
    );
    expect(heldStandKey(broken)).toBeNull();
  });
});
