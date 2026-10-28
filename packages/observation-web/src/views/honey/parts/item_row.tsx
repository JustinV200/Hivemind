/**
 * `ItemRow`: one item of a folder, enough to weigh it before opening it.
 *
 * The row leads with the clearance tag and the kind so the label is the first thing read at any
 * width, then the title as the link that opens the item in the reading pane (its address is the
 * folder's with `?item=<id>`, so the open pane can be shared and reloaded), then the detail, the
 * wax fields and the provenance. The link's name is the title alone, so a screen reader hears what
 * it opens and the label separately.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/honey/parts/`. Used by the folder panel's list.
 *
 * Key invariants:
 *   - The clearance tag is always drawn, outside the button, and never hidden at any width.
 *   - Opening is a navigation to the item's address; the row reads nothing and writes nothing.
 *
 * See Also:
 *   - docs/observation/design.md section 5.7.
 */
import type { ReactElement } from 'react';
import type { ItemSummary } from '../slice';
import { ClearanceBadge } from './clearance_badge';
import { Provenance } from './provenance';
import { WaxFacts } from './wax_facts';
import { itemHref, kindLabel } from './words';

export function ItemRow(props: { item: ItemSummary; open: boolean; path: string }): ReactElement {
  const { item, open, path } = props;
  return (
    <li className="honey-item" aria-current={open ? 'true' : undefined}>
      <div className="honey-item-head">
        <ClearanceBadge label={item.clearance} />
        <span className="honey-kind">{kindLabel(item.kind)}</span>
        <a className="honey-open" href={itemHref(path, item.id)}>
          {item.title}
        </a>
      </div>
      {/* A wax note's detail is its severity, which the wax line below already shows. */}
      {item.detail && item.kind !== 'wax' ? <p className="honey-muted">{item.detail}</p> : null}
      <WaxFacts item={item} />
      <Provenance source={item.provenance} />
    </li>
  );
}
