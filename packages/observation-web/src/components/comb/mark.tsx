/**
 * `Mark`: the Observation Hive's one brand mark, a hexagon cell holding a smaller filled one.
 *
 * The hive shows itself through one recurring shape, the comb cell, rather than a mascot or
 * stripes. The mark is an outlined hexagon with a filled hexagon inside: the outer line is the
 * cell, the inner fill is the honey in it. It is drawn in the current text colour (the outline)
 * and the accent (the fill), so it works on the dark chrome and on a cream card alike.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/components/comb/`. Used by the top bar and `BrandHeader`; the
 *   same two hexagons are the PWA icon under `public/icons/`.
 *
 * Key invariants:
 *   - Decorative: `aria-hidden`, because the name beside it is the content.
 *   - Tokens only: `currentColor` for the outline, `var(--accent)` for the fill.
 *
 * See Also:
 *   - docs/observation/design.md section 3.4 (the honeycomb motif and where it may appear).
 */
import type { ReactElement } from 'react';

// A flat-top hexagon of radius 1 around the origin, as path data; scaled by the viewBox.
export const HEXAGON = 'M12 2 20.66 7v10L12 22 3.34 17V7z';
const INNER = 'M12 7.5 15.9 9.75v4.5L12 16.5 8.1 14.25v-4.5z';

export function Mark(props: { size?: number }): ReactElement {
  const size = props.size ?? 24;
  return (
    <svg
      className="mark"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      aria-hidden="true"
      focusable="false"
    >
      <path d={HEXAGON} fill="none" stroke="currentColor" strokeWidth="1.75" />
      <path d={INNER} fill="var(--accent)" />
    </svg>
  );
}
