/**
 * The Attendant view's placeholder: the title row and nothing else, for now.
 *
 * This view will show the task graph and ordered inbox for the Queen or one Warden. It is reached
 * at `#/attendant/<principal>`. Until its own roadmap step lands it renders the shared title row
 * only, so the shell, the nav, the router and the layout can be exercised on every route from the
 * start.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/attendant/`. Chosen by the outlet in `src/shell/` when
 *   the route is `attendant`. Built out in step 12.7, to design.md section 5.5.
 *
 * Key invariants:
 *   - The `principal` from the route is shown as the subject, so the route table can be checked
 *     before the view is built.
 *   - It makes no request and reads no slice, so it cannot go stale or write anywhere.
 *
 * See Also:
 *   - docs/observation/design.md section 5.5, for what this view will hold.
 */
import type { ReactElement } from 'react';
import { ViewTitle } from '../../components';

export function AttendantView(props: { principal: string }): ReactElement {
  return <ViewTitle title="Attendant" subject={props.principal} />;
}
