/**
 * `ViewTitle`: the title row every view starts with, led by the one recurring comb mark.
 *
 * A view has a title row (its title, then its filters) and then its body (design.md section 2).
 * The title is preceded by a small hexagon in the accent colour: the single mark that recurs on
 * every screen, so the hive is felt without a mascot. An optional subject (a Cell id, a bee, a
 * Honey path) is shown in monospace beside the title. Under the row, an optional `hint` says in
 * one plain sentence what the view shows, so no screen opens on a bare bee word; and while a
 * live stream has dropped, a note says `stale since hh:mm:ss, reconnecting`, so rows drawn from
 * the last frames are never taken for current ones (design.md section 8).
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/components/`. Used by every view in `src/views/`; the stale
 *   note reads the connection slice, so it renders only inside the store's provider.
 *
 * Key invariants:
 *   - The title is an `h1`, one per screen, so the page has a single top heading, and the glyph
 *     is hidden from assistive technology so the heading's name is the words alone.
 *   - Filters, when a view adds them, go in `children` after the title; the hint and the stale
 *     note are full-width lines after them.
 *   - The note's time is the connection slice's, never a clock read here.
 *
 * See Also:
 *   - docs/observation/design.md sections 2, 3.4, 5 and 8.
 *   - src/store/slices/connection.ts for `viewStaleSince`.
 */
import type { ReactElement, ReactNode } from 'react';
import { useSlice, viewStaleSince } from '../store';
import { clockWords } from './time';

// A flat-top hexagon on a 10 by 10 grid, drawn in the accent before the title.
const GLYPH = 'M5 0 9.33 2.5v5L5 10 .67 7.5v-5z';

// The line that says the view is behind: drawn only while a stream has dropped, with the time
// in the strip's own words (`clockWords`), so the two notes never read differently.
function StaleNote(): ReactElement | null {
  const since = viewStaleSince(useSlice('connection'));
  if (since === null) return null;
  return (
    <p className="view-stale" role="status">
      {`stale since ${clockWords(since)}, reconnecting`}
    </p>
  );
}

export function ViewTitle(props: {
  title: string;
  subject?: string;
  hint?: string;
  children?: ReactNode;
}): ReactElement {
  return (
    <div className="view-title">
      <h1>
        <svg className="view-glyph" viewBox="0 0 10 10" aria-hidden="true" focusable="false">
          <path d={GLYPH} fill="var(--accent)" />
        </svg>
        {props.title}
      </h1>
      {props.subject ? <code className="view-subject">{props.subject}</code> : null}
      {props.children}
      {props.hint ? <p className="view-hint">{props.hint}</p> : null}
      <StaleNote />
    </div>
  );
}
