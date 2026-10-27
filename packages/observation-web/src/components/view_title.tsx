/**
 * `ViewTitle`: the title row every view starts with, led by the one recurring comb mark.
 *
 * A view has a title row (its title, then its filters) and then its body (design.md section 2).
 * The title is preceded by a small hexagon in the accent colour: the single mark that recurs on
 * every screen, so the hive is felt without a mascot. An optional subject (a Cell id, a bee, a
 * Honey path) is shown in monospace beside the title.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/components/`. Used by every view in `src/views/`.
 *
 * Key invariants:
 *   - The title is an `h1`, one per screen, so the page has a single top heading, and the glyph
 *     is hidden from assistive technology so the heading's name is the words alone.
 *   - Filters, when a view adds them, go in `children` after the title.
 *
 * See Also:
 *   - docs/observation/design.md sections 2, 3.4 and 5.
 */
import type { ReactElement, ReactNode } from 'react';

// A flat-top hexagon on a 10 by 10 grid, drawn in the accent before the title.
const GLYPH = 'M5 0 9.33 2.5v5L5 10 .67 7.5v-5z';

export function ViewTitle(props: {
  title: string;
  subject?: string;
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
    </div>
  );
}
