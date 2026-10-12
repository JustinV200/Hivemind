/**
 * `HoneyPanel`: the way into the Honey this Cell's Warden can see.
 *
 * The page names the Honey browser folder for what the Cell can see; this panel is a link to it,
 * and nothing more, so reading the Honey (and its clearance and provenance) happens in the Honey
 * browser where the viewer's own scopes apply.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cell/panels/`. Rendered by `cell.tsx`; the Honey view
 *   itself is roadmap step 12.9.
 *
 * Key invariants:
 *   - The link is built from the page's `honey_folder` with its leading slash removed, because the
 *     route is `#/honey/<path>` and the folder is `/cells/<id>`.
 *
 * See Also:
 *   - docs/observation/design.md sections 5.2 and 5.7.
 */
import type { ReactElement } from 'react';
import { hrefFor } from '../../../router';
import type { CellPage } from '../slice';
import { Panel } from './panel';

export function HoneyPanel(props: { page: CellPage }): ReactElement {
  const path = props.page.honey_folder.replace(/^\/+/, '');
  return (
    <Panel id="honey" title="Honey">
      <p>
        <a href={hrefFor({ name: 'honey', path })}>Browse the Honey this Cell can see</a>
      </p>
    </Panel>
  );
}
