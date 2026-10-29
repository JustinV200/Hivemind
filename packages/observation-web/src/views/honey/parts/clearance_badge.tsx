/**
 * `ClearanceBadge`: an item's `HoneyClearance` as a labelled tag that is always on screen.
 *
 * Every item in the Honey browser carries a sensitivity label, and the viewer must see it without
 * opening anything. The tag always carries its words (`C2 Royal`), so colour is never the signal;
 * its box outline (a square tag with a heavier left edge) tells it apart from a tier pill and a
 * mask tag. It is never a button; its `title` repeats the README's one line on the label, and
 * `ClearanceLegend` draws the same three lines in the open, so nothing important is hover-only.
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
import { CLEARANCE_MEANINGS, clearanceWords } from '../../../components';
import type { HoneyClearance } from '../../../components';
import { hrefFor } from '../../../router';

export type { HoneyClearance };

// The glossary entry on the labels, for the reader who wants more than a line each.
const HELP_HREF = hrefFor({ name: 'help', page: 'glossary', section: 'honey-clearance' });

export function ClearanceBadge(props: { label: HoneyClearance }): ReactElement {
  const { label } = props;
  return (
    <span
      className={`honey-clearance honey-clearance--${label.toLowerCase()}`}
      title={CLEARANCE_MEANINGS[label]}
    >
      {clearanceWords(label)}
    </span>
  );
}

// The fixed key to the three labels, drawn under the Honey title at every width, like the
// Fleet's legend: the badge, then the README's one line on what it means.
export function ClearanceLegend(): ReactElement {
  return (
    <section className="honey-legend" aria-labelledby="honey-legend-title">
      <h2 id="honey-legend-title">Clearance labels</h2>
      <a href={HELP_HREF}>More in Help</a>
      <ul>
        {(['C0', 'C1', 'C2'] as const).map((label) => (
          <li key={label}>
            <ClearanceBadge label={label} />
            <span>{CLEARANCE_MEANINGS[label]}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
