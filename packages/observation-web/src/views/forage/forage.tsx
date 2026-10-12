/**
 * The Forage view's placeholder: the title row and nothing else, for now.
 *
 * This view will show the Forage flow, per-host capacity, the Forage map, the Fanner's queues and
 * pending requests. It is reached at `#/forage`. Until its own roadmap step lands it renders the
 * shared title row only, so the shell, the nav, the router and the layout can be exercised on every
 * route from the start.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/forage/`. Chosen by the outlet in `src/shell/` when the
 *   route is `forage`. Built out in step 12.5, to design.md section 5.3.
 *
 * Key invariants:
 *   - Renders only the title row until the view itself is built.
 *   - It makes no request and reads no slice, so it cannot go stale or write anywhere.
 *
 * See Also:
 *   - docs/observation/design.md section 5.3, for what this view will hold.
 */
import type { ReactElement } from 'react';
import { ViewTitle } from '../../components';

export function ForageView(): ReactElement {
  return <ViewTitle title="Forage" />;
}
