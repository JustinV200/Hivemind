/**
 * The nav: a bottom bar on a phone, a left rail from 720px.
 *
 * One `<nav>` landmark holds every item; CSS decides how it is laid out. On a phone the first
 * five items show and a "More" item opens the rest in a panel above the bar, with the device
 * name and the log out button that the top bar has no room for there; the item then reads
 * "Close". From 720px all items show in a rail, with a faint comb filling the rail below them. The
 * current item carries `aria-current="page"` and the chrome accent with a rule beside it. On a phone in landscape the bar slides away while
 * scrolling down and returns on scrolling up, to give the page its height; the summary strip is
 * not part of the nav and never hides.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/shell/`. Rendered by `shell.tsx`; reads the route from
 *   `src/router/` and the listener from the session slice.
 *
 * Key invariants:
 *   - The only state is whether "More" is open and whether the bar is scrolled away.
 *   - Choosing a link closes "More".
 *   - Enrol appears only when the session's listener is `loopback`.
 *   - The log out button exists once in the page: here on a phone, in the top bar on a rail.
 *
 * See Also:
 *   - docs/observation/design.md section 2 for the nav.
 *   - src/shell/nav_items.tsx for the items.
 */
import { useEffect, useState } from 'react';
import type { ReactElement } from 'react';
import { HexPattern, RAIL_MIN, useMinWidth } from '../components';
import { useRoute } from '../router';
import { useSlice } from '../store';
import { DeviceLine, useLogOut } from './head';
import { CLOSE_ICON, Icon, MORE_ICON, visibleItems } from './nav_items';

// True while the person is scrolling down; the stylesheet applies it to landscape phones only.
function useScrolledDown(): boolean {
  const [down, setDown] = useState(false);
  useEffect(() => {
    let last = window.scrollY;
    const onScroll = (): void => {
      const now = window.scrollY;
      setDown(now > last && now > 48);
      last = now;
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);
  return down;
}

// What the phone's bar has no room for: the device line and the log out button, in the panel.
function MorePanel(): ReactElement {
  const logOut = useLogOut();
  return (
    <div className="nav-extra">
      <DeviceLine />
      <button type="button" className="button" onClick={logOut}>
        Log out
      </button>
    </div>
  );
}

export function Nav(): ReactElement {
  const route = useRoute();
  const { listener } = useSlice('session');
  const [moreOpen, setMoreOpen] = useState(false);
  const scrolledDown = useScrolledDown();
  const wide = useMinWidth(RAIL_MIN);
  const classes = ['nav', moreOpen ? 'nav--more-open' : '', scrolledDown ? 'nav--hidden' : ''];

  return (
    <nav className={classes.filter(Boolean).join(' ')} aria-label="Main">
      <ul>
        {visibleItems(listener).map((item) => (
          <li key={item.label} className={item.primary ? undefined : 'nav-secondary'}>
            <a
              href={item.href}
              aria-current={item.owns.includes(route.name) ? 'page' : undefined}
              onClick={() => setMoreOpen(false)}
            >
              <Icon path={item.icon} />
              <span>{item.label}</span>
            </a>
          </li>
        ))}
        <li className="nav-more-slot">
          <button
            type="button"
            className="nav-more"
            aria-expanded={moreOpen}
            onClick={() => setMoreOpen(!moreOpen)}
          >
            <Icon path={moreOpen ? CLOSE_ICON : MORE_ICON} />
            <span>{moreOpen ? 'Close' : 'More'}</span>
          </button>
        </li>
      </ul>
      {moreOpen && !wide ? <MorePanel /> : null}
      {/* The rail's empty lower half carries a faint comb; on a phone's bar there is no room. */}
      <div className="nav-comb">
        <HexPattern tone="chrome" />
      </div>
    </nav>
  );
}
