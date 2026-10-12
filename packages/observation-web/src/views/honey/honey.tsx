/**
 * The Honey view's placeholder: the title row and nothing else, for now.
 *
 * This view will show the Honey browser: a folder tree and the folder's items, as the viewer's
 * scopes allow. It is reached at `#/honey/<path>`. Until its own roadmap step lands it renders the
 * shared title row only, so the shell, the nav, the router and the layout can be exercised on every
 * route from the start.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/honey/`. Chosen by the outlet in `src/shell/` when the
 *   route is `honey`. Built out in step 12.9, to design.md section 5.7.
 *
 * Key invariants:
 *   - The `path` from the route is shown as the subject, so the route table can be checked before
 *     the view is built.
 *   - It makes no request and reads no slice, so it cannot go stale or write anywhere.
 *
 * See Also:
 *   - docs/observation/design.md section 5.7, for what this view will hold.
 */
import type { ReactElement } from 'react';
import { ViewTitle } from '../../components';

export function HoneyView(props: { path: string }): ReactElement {
  return <ViewTitle title="Honey" subject={props.path} />;
}
