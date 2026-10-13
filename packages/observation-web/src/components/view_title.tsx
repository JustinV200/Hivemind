/**
 * `ViewTitle`: the title row every view starts with.
 *
 * A view has a title row (its title, then its filters) and then its body (design.md section 2).
 * Until a view's own step lands, its directory renders this alone, so the shell, routing and
 * layout can be checked on every route. An optional subject (a Cell id, a bee, a Honey path) is
 * shown in monospace beside the title.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/components/`. Used by every placeholder in `src/views/`.
 *
 * Key invariants:
 *   - The title is an `h1`, one per screen, so the page has a single top heading.
 *   - Filters, when a view adds them, go in `children` after the title.
 *
 * See Also:
 *   - docs/observation/design.md sections 2 and 5.
 */
import type { ReactElement, ReactNode } from 'react';

export function ViewTitle(props: {
  title: string;
  subject?: string;
  children?: ReactNode;
}): ReactElement {
  return (
    <div className="view-title">
      <h1>{props.title}</h1>
      {props.subject ? <code className="view-subject">{props.subject}</code> : null}
      {props.children}
    </div>
  );
}
