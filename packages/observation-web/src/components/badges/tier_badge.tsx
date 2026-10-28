/**
 * `TierBadge`: a Cell's Comb Shield level as a rounded pill.
 *
 * Operators must tell Meadow, Propolis and Night Veil apart at a glance without opening a detail
 * panel, so the level is a persistent badge. Colour alone is never the signal: the pill always
 * carries its words, and its fully rounded outline marks it as a tier (a Pheromone Mask is a
 * square tag instead, see `mask_badge.tsx`), so a reader who cannot tell the colours still tells
 * the two kinds apart.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/components/badges/`. Used by the Fleet rows and the Cell page header
 *   in later steps; styled by `src/styles/components.css` from the badge tokens.
 *
 * Key invariants:
 *   - The label is the README's word with the underscore as a space (`NIGHT VEIL`).
 *   - The badge is never a button and has no hover-only content.
 *
 * See Also:
 *   - docs/observation/design.md sections 3.3 and 4.1.
 */
import type { ReactElement } from 'react';
import type { components } from '../../landing_board/types';

export type CombShieldLevel = components['schemas']['CombShieldLevel'];

// The CSS modifier for each level; the token names are --tier-<modifier>-bg and -fg.
const MODIFIER: Record<CombShieldLevel, string> = {
  MEADOW: 'meadow',
  PROPOLIS: 'propolis',
  NIGHT_VEIL: 'night-veil',
};

export function TierBadge(props: { level: CombShieldLevel }): ReactElement {
  return (
    <span className={`badge badge-tier badge-tier--${MODIFIER[props.level]}`}>
      {props.level.replace('_', ' ')}
    </span>
  );
}
