/**
 * The nav's items: what each is called, where it goes, and its icon.
 *
 * The nav lists Fleet, Forage, Thoughts, Attendant, Chat, Honey, Cost, Capping and Devices, with
 * Enrol added only on the Hive Stand (design.md section 2). The first five are `primary`: they
 * are the bottom bar on a phone, and the rest sit behind a "More" item there. This file is data
 * and tiny icon components, so the nav itself stays about behaviour.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/shell/`. Read by `nav.tsx`.
 *
 * Key invariants:
 *   - Each item's `href` is a hash route that the router resolves (never `#main` or the like).
 *   - `owns` lists the route names whose screens belong to the item, so the Cell page keeps
 *     Fleet marked current.
 *   - Icons are single-path SVG, hidden from assistive technology; the label is the content.
 *   - `hint` is the view's own gloss from `VIEW_HINTS`, so the nav and the title row say the same.
 *
 * See Also:
 *   - docs/observation/design.md section 2 for the nav list.
 *   - src/router/routes.ts for the routes.
 */
import type { ReactElement } from 'react';
import { VIEW_HINTS } from '../components';
import type { Listener } from '../store';
import type { RouteName } from '../router';

export interface NavItem {
  label: string;
  href: string;
  owns: readonly RouteName[];
  primary: boolean;
  loopbackOnly: boolean;
  icon: string;
  // The one-line gloss the item's view opens with, as the link's `title`.
  hint: string;
}

// 24 by 24 outline paths, drawn with a 2px round stroke in the current text colour.
export const NAV_ITEMS: readonly NavItem[] = [
  {
    label: 'Fleet',
    href: '#/fleet',
    owns: ['fleet', 'cell'],
    primary: true,
    loopbackOnly: false,
    icon: 'M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z',
    hint: VIEW_HINTS.fleet,
  },
  {
    label: 'Forage',
    href: '#/forage',
    owns: ['forage'],
    primary: true,
    loopbackOnly: false,
    icon: 'M3 6h9l3 3h6M3 12h18M3 18h9l3-3h6',
    hint: VIEW_HINTS.forage,
  },
  {
    label: 'Thoughts',
    href: '#/thoughts/queen',
    owns: ['thoughts'],
    primary: true,
    loopbackOnly: false,
    icon: 'M12 3a6 6 0 0 0-3 11v3h6v-3a6 6 0 0 0-3-11zM9 20h6',
    hint: VIEW_HINTS.thoughts,
  },
  {
    label: 'Attendant',
    href: '#/attendant/queen',
    owns: ['attendant'],
    primary: true,
    loopbackOnly: false,
    icon: 'M4 6h2M9 6h11M4 12h2M9 12h11M4 18h2M9 18h11',
    hint: VIEW_HINTS.attendant,
  },
  {
    label: 'Chat',
    href: '#/chat',
    owns: ['chat'],
    primary: true,
    loopbackOnly: false,
    icon: 'M3 4h18v13H9l-6 4z',
    hint: VIEW_HINTS.chat,
  },
  {
    label: 'Honey',
    href: '#/honey',
    owns: ['honey'],
    primary: false,
    loopbackOnly: false,
    icon: 'M12 3l7 4v10l-7 4-7-4V7z',
    hint: VIEW_HINTS.honey,
  },
  {
    label: 'Cost',
    href: '#/cost',
    owns: ['cost'],
    primary: false,
    loopbackOnly: false,
    icon: 'M12 3v18M16 7h-6a3 3 0 0 0 0 6h4a3 3 0 0 1 0 6H8',
    hint: VIEW_HINTS.cost,
  },
  {
    label: 'Capping',
    href: '#/capping',
    owns: ['capping'],
    primary: false,
    loopbackOnly: false,
    icon: 'M12 3l8 3v6c0 5-4 8-8 9-4-1-8-4-8-9V6z',
    hint: VIEW_HINTS.capping,
  },
  {
    label: 'Devices',
    href: '#/devices',
    owns: ['devices'],
    primary: false,
    loopbackOnly: false,
    icon: 'M7 3h10v18H7zM11 18h2',
    hint: VIEW_HINTS.devices,
  },
  {
    label: 'Enrol',
    href: '#/enrol',
    owns: ['enrol'],
    primary: false,
    loopbackOnly: true,
    icon: 'M12 5v14M5 12h14',
    hint: VIEW_HINTS.enrol,
  },
];

export const MORE_ICON = 'M5 12h.01M12 12h.01M19 12h.01';
export const CLOSE_ICON = 'M6 6l12 12M18 6 6 18';

export function visibleItems(listener: Listener | null): NavItem[] {
  return NAV_ITEMS.filter((item) => !item.loopbackOnly || listener === 'loopback');
}

export function Icon(props: { path: string }): ReactElement {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={props.path} />
    </svg>
  );
}
