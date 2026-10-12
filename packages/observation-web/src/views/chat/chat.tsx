/**
 * The Chat view's placeholder: the title row and nothing else, for now.
 *
 * This view will show the conversation with the Queen, the one write path into her inbox. It is
 * reached at `#/chat`. Until its own roadmap step lands it renders the shared title row only, so
 * the shell, the nav, the router and the layout can be exercised on every route from the start.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/chat/`. Chosen by the outlet in `src/shell/` when the
 *   route is `chat`. Built out in step 12.8, to design.md section 5.6.
 *
 * Key invariants:
 *   - Renders only the title row until the view itself is built.
 *   - It makes no request and reads no slice, so it cannot go stale or write anywhere.
 *
 * See Also:
 *   - docs/observation/design.md section 5.6, for what this view will hold.
 */
import type { ReactElement } from 'react';
import { ViewTitle } from '../../components';

export function ChatView(): ReactElement {
  return <ViewTitle title="Chat" />;
}
