/**
 * `MaskBadge`: a Cell's Pheromone Mask state as a square tag.
 *
 * The mask is `OFF`, `WARDEN` or `QUEEN_FORCED`, and "no mask" is information, so `OFF` is drawn
 * too, in grey. A Queen-forced mask also shows when it expires, in muted text beside the tag.
 * The tag is square-cornered with a small square glyph at its left (set in CSS), the opposite of
 * the tier pill, so shape tells the kind and the words tell the value.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/components/badges/`. Used by the Fleet rows and the Cell page header
 *   in later steps; styled by `src/styles/components.css` from the badge tokens.
 *
 * Key invariants:
 *   - The label is the README's word with the underscore as a space (`QUEEN FORCED`).
 *   - The expiry shows only for a Queen-forced mask, as `until hh:mm` in local time, with the full
 *     ISO time in the `<time>` element.
 *
 * See Also:
 *   - docs/observation/design.md sections 3.3 and 4.1.
 */
import type { ReactElement } from 'react';
import type { components } from '../../landing_board/types';

export type MaskState = NonNullable<components['schemas']['CellView']['mask_state']>;

const MODIFIER: Record<MaskState, string> = {
  OFF: 'off',
  WARDEN: 'warden',
  QUEEN_FORCED: 'queen-forced',
};

function clock(iso: string): string {
  const date = new Date(iso);
  const pad = (n: number): string => String(n).padStart(2, '0');
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function MaskBadge(props: { state: MaskState; until?: string | null }): ReactElement {
  const showUntil = props.state === 'QUEEN_FORCED' && props.until;
  return (
    <span className="badge-group">
      <span className={`badge badge-mask badge-mask--${MODIFIER[props.state]}`}>
        {props.state.replace('_', ' ')}
      </span>
      {showUntil ? (
        <time className="badge-until" dateTime={props.until ?? undefined}>
          until {clock(props.until ?? '')}
        </time>
      ) : null}
    </span>
  );
}
