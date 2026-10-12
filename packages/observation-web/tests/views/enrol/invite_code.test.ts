/**
 * Tests for reading an invite out of the page's address.
 *
 * The link the Entrance mints is `<base>/enrol#code=<code>`, and the app's own hash routes could
 * carry the code after a `?`. Anything else, including the word `code` inside another fragment, is
 * not an invite.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/enrol/join/invite_code.ts.
 *
 * Key invariants:
 *   - Only a code of the Entrance's shape is returned.
 *
 * See Also:
 *   - docs/entrance/landing-board.md section 4.
 */
import { afterEach, describe, expect, it } from 'vitest';
import { inviteCodeFrom } from '../../../src/views/enrol/join';
import { leaveInvite } from '../../../src/views/enrol/join/invite_code';

const CODE = 'ABCD-EFGH-IJKL-MNOP-QRST-UVWX-YZ';

afterEach(() => {
  window.history.replaceState(null, '', '/');
});

describe('inviteCodeFrom', () => {
  it.each([
    [`#code=${CODE}`, CODE],
    [`#/enrol?code=${CODE}`, CODE],
    [`#/enrol?x=1&code=${CODE}&y=2`, CODE],
    [`#code=${CODE.toLowerCase()}`, CODE.toLowerCase()],
  ])('reads %s', (hash, code) => {
    expect(inviteCodeFrom({ hash })).toBe(code);
  });

  it.each([
    [''],
    ['#/fleet'],
    ['#code='],
    ['#code=short'],
    ['#mycode=ABCDEFGHIJKL'],
    [`#code=${'A'.repeat(65)}`],
    ['#code=AB CD EF GH'],
    ['#code=AAAAAAAA<script>'],
  ])('reads no invite in %j', (hash) => {
    expect(inviteCodeFrom({ hash })).toBeNull();
  });
});

describe('leaveInvite', () => {
  it('drops the fragment without adding a history entry, and sends /enrol back to the root', () => {
    window.history.replaceState(null, '', `/enrol?a=1#code=${CODE}`);
    const length = window.history.length;
    leaveInvite();
    expect(window.location.hash).toBe('');
    expect(window.location.pathname).toBe('/');
    expect(window.location.search).toBe('?a=1');
    expect(window.history.length).toBe(length);
  });
});
