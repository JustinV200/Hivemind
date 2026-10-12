/**
 * The Enrol view's placeholder: the title row and nothing else, for now.
 *
 * This view will show the loopback-only enrolment screens: invite, pending, approve, deny, unlock,
 * revoke and reopen. It is reached at `#/enrol`. Until its own roadmap step lands it renders the
 * shared title row only, so the shell, the nav, the router and the layout can be exercised on every
 * route from the start.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/enrol/`. Chosen by the outlet in `src/shell/` when the
 *   route is `enrol`. Built out in step 12.8a, to design.md section 5.10.
 *
 * Key invariants:
 *   - Renders only the title row until the view itself is built.
 *   - It makes no request and reads no slice, so it cannot go stale or write anywhere.
 *
 * See Also:
 *   - docs/observation/design.md section 5.10, for what this view will hold.
 */
import type { ReactElement } from 'react';
import { ViewTitle } from '../../components';

export function EnrolView(): ReactElement {
  return <ViewTitle title="Enrol" />;
}
