/**
 * Read the invite out of the page's address, and take it out again once it is spent.
 *
 * An invite link is `<base>/enrol#code=<code>` (landing-board.md section 4): the code rides the
 * fragment so it is never sent to a server in a request line or kept in an access log. The page may
 * also be reached through the app's own hash route (`#/enrol?code=...`), so the code is looked for
 * after `#`, `?` or `&`. Only a code of the shape the Entrance mints is accepted (letters, digits
 * and dashes, short), so a fragment that merely contains the word is not mistaken for one.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/enrol/join/`. Read by the login screen to decide whether
 *   to show the join screen, and by `use_join.ts` to take the spent code out of the address.
 *
 * Key invariants:
 *   - Pure reading: `inviteCodeFrom` takes the location it is given and touches nothing else.
 *   - `leaveInvite` removes the fragment without reloading and without adding a history entry.
 *   - The code is never logged or stored.
 *
 * See Also:
 *   - docs/entrance/landing-board.md section 4.
 */

// Letters, digits and dashes; the Entrance's codes are 26 characters in groups, 64 is its ceiling.
const CODE = /[#?&]code=([A-Za-z0-9-]{8,64})(?:[&]|$)/;

export function inviteCodeFrom(location: Pick<Location, 'hash'>): string | null {
  return CODE.exec(location.hash)?.[1] ?? null;
}

// Drops the spent code from the address; `/enrol` goes back to the app's root.
export function leaveInvite(): void {
  const { pathname, search } = window.location;
  window.history.replaceState(null, '', pathname.replace(/\/enrol\/?$/, '/') + search);
}
