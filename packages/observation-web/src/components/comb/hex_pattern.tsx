/**
 * `HexPattern`: a faint comb drawn behind a surface, as an SVG pattern of hexagon outlines.
 *
 * The honeycomb is structure, not decoration, so it appears in a few fixed places only: behind
 * the login card and behind an empty state, never behind a table or running text. The cells are
 * 28 px across and drawn as strokes of the border token at low opacity, so the pattern is felt
 * more than read and never fights the words in front of it.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/components/comb/`. Used by the login screen and `EmptyState`;
 *   positioned by `.hex-pattern` in `src/styles/components.css`.
 *
 * Key invariants:
 *   - Decorative: `aria-hidden` and `pointer-events: none`, so it is never read or clicked.
 *   - Tokens only, and the opacity is low enough that any text over it keeps its contrast.
 *   - Pattern ids are unique per instance, so two patterns on one page do not share a `<defs>`.
 *
 * See Also:
 *   - docs/observation/design.md section 3.4.
 */
import { useId } from 'react';
import type { ReactElement } from 'react';

// One flat-top cell in a 28 by 48.5 tile, with the half-offset cell that tiles the comb.
const CELL = 'M14 0 28 8.08v16.17L14 32.33 0 24.25V8.08z';
const TILE_W = 28;
const TILE_H = 48.5;

export function HexPattern(): ReactElement {
  const id = useId();
  return (
    <svg className="hex-pattern" aria-hidden="true" focusable="false">
      <defs>
        <pattern id={id} width={TILE_W} height={TILE_H} patternUnits="userSpaceOnUse">
          <path d={CELL} fill="none" stroke="var(--border)" strokeWidth="1" />
          <path
            d={CELL}
            transform={`translate(14 ${TILE_H / 2})`}
            fill="none"
            stroke="var(--border)"
            strokeWidth="1"
          />
        </pattern>
      </defs>
      <rect width="100%" height="100%" fill={`url(#${id})`} />
    </svg>
  );
}
