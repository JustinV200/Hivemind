/**
 * The Cell view's placeholder: the title row and nothing else, for now.
 *
 * This view will show one Cell's header (with the tier and mask badges), diagram, tasks, Forage,
 * Honey, Cell Wax and Capping activity. It is reached at `#/cells/<id>`. Until its own roadmap step
 * lands it renders the shared title row only, so the shell, the nav, the router and the layout can
 * be exercised on every route from the start.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cell/`. Chosen by the outlet in `src/shell/` when the
 *   route is `cell`. Built out in step 12.4, to design.md section 5.2.
 *
 * Key invariants:
 *   - The `id` from the route is shown as the subject, so the route table can be checked before the
 *     view is built.
 *   - It makes no request and reads no slice, so it cannot go stale or write anywhere.
 *
 * See Also:
 *   - docs/observation/design.md section 5.2, for what this view will hold.
 */
import type { ReactElement } from 'react';
import { ViewTitle } from '../../components';

export function CellView(props: { id: string }): ReactElement {
  return <ViewTitle title="Cell" subject={props.id} />;
}
