/**
 * The Fleet view's placeholder: the title row and nothing else, for now.
 *
 * This view will show all Cells in one table with a Real, Virtual or All filter, the tier and mask
 * badges and the legend. It is reached at `#/fleet`. Until its own roadmap step lands it renders
 * the shared title row only, so the shell, the nav, the router and the layout can be exercised on
 * every route from the start.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/fleet/`. Chosen by the outlet in `src/shell/` when the
 *   route is `fleet`. Built out in step 12.6, to design.md section 5.1.
 *
 * Key invariants:
 *   - Renders only the title row until the view itself is built.
 *   - It makes no request and reads no slice, so it cannot go stale or write anywhere.
 *
 * See Also:
 *   - docs/observation/design.md section 5.1, for what this view will hold.
 */
import type { ReactElement } from 'react';
import { ViewTitle } from '../../components';

export function FleetView(): ReactElement {
  return <ViewTitle title="Fleet" />;
}
