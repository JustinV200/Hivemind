/**
 * The Thoughts view's placeholder: the title row and nothing else, for now.
 *
 * This view will show a bee's episodes, newest first, with a Follow toggle (the Queen's at
 * `#/thoughts/queen`). It is reached at `#/thoughts/<bee>`. Until its own roadmap step lands it
 * renders the shared title row only, so the shell, the nav, the router and the layout can be
 * exercised on every route from the start.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/thoughts/`. Chosen by the outlet in `src/shell/` when the
 *   route is `thoughts`. Built out in step 12.3, to design.md section 5.4.
 *
 * Key invariants:
 *   - The `bee` from the route is shown as the subject, so the route table can be checked before
 *     the view is built.
 *   - It makes no request and reads no slice, so it cannot go stale or write anywhere.
 *
 * See Also:
 *   - docs/observation/design.md section 5.4, for what this view will hold.
 */
import type { ReactElement } from 'react';
import { ViewTitle } from '../../components';

export function ThoughtsView(props: { bee: string }): ReactElement {
  return <ViewTitle title="Thoughts" subject={props.bee} />;
}
