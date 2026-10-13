/**
 * The nav: a bottom bar on a phone, a left rail from 720px.
 *
 * One `<nav>` landmark holds every item; CSS decides how it is laid out. On a phone the first
 * five items show and a "More" item opens the rest in a second row; from 720px all items show in
 * a rail. The current item carries `aria-current="page"` and the accent colour. On a phone in
 * landscape the bar slides away while scrolling down and returns on scrolling up, to give the
 * page its height; the summary strip is not part of the nav and never hides.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/shell/`. Rendered by `shell.tsx`; reads the route from
 *   `src/router/` and the listener from the session slice.
 *
 * Key invariants:
 *   - The only state is whether "More" is open and whether the bar is scrolled away.
 *   - Choosing a link closes "More".
 *   - Enrol appears only when the session's listener is `loopback`.
 *
 * See Also:
 *   - docs/observation/design.md section 2 for the nav.
 *   - src/shell/nav_items.tsx for the items.
 */
import { useEffect, useState } from 'react';
import type { ReactElement } from 'react';
import { useRoute } from '../router';
import { useSlice } from '../store';
import { Icon, MORE_ICON, visibleItems } from './nav_items';

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

export function Nav(): ReactElement {
  const route = useRoute();
  const { listener } = useSlice('session');
  const [moreOpen, setMoreOpen] = useState(false);
  const scrolledDown = useScrolledDown();
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
            <Icon path={MORE_ICON} />
            <span>More</span>
          </button>
        </li>
      </ul>
    </nav>
  );
}
