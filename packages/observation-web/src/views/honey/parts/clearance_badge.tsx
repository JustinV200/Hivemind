/**
 * `ClearanceBadge`: an item's `HoneyClearance` as a labelled tag that is always on screen.
 *
 * Every item in the Honey browser carries a sensitivity label, and the viewer must see it without
 * opening anything. The tag always carries its words (`C2 Royal`), so colour is never the signal;
 * its box outline (a square tag with a heavier left edge) tells it apart from a tier pill and a
 * mask tag. It is never a button and has no hover-only content.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/honey/`. Used by item rows, search hits, the reading
 *   pane and the folder's ceiling line; styled by `honey.css` from the surface and status tokens.
 *
 * Key invariants:
 *   - The visible text is the label and its README name, for all three labels.
 *   - Holds no state and reads no slice.
 *
 * See Also:
 *   - README.md (Honey clearance) and docs/observation/design.md sections 3.3 and 5.7.
 */
import type { ReactElement } from 'react';
import type { components } from '../../../landing_board/types';
import { CLEARANCE_NAMES } from './words';

export type HoneyClearance = components['schemas']['HoneyClearance'];

export function ClearanceBadge(props: { label: HoneyClearance }): ReactElement {
  const { label } = props;
  return (
    <span className={`honey-clearance honey-clearance--${label.toLowerCase()}`}>
      {`${label} ${CLEARANCE_NAMES[label]}`}
    </span>
  );
}
