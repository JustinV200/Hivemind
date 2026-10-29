/**
 * `Breadcrumbs`: the path to the open folder, each step a link, and a way up.
 *
 * The crumbs come from the route's path, so they are right before the folder has been read. The
 * last crumb is the folder itself and is not a link. Once the folder is read its `parent` (null
 * only for the root) gives the "Up" link, which is the Entrance's own word on where up is. At
 * the root nothing is drawn: the one crumb would repeat the title.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/honey/parts/`. Used by the Honey screen under the title.
 *
 * Key invariants:
 *   - A landmark `nav` with an accessible name; the current step carries `aria-current="page"`.
 *   - Links are hash links the router resolves; nothing here reads or writes the store.
 *
 * See Also:
 *   - src/views/honey/parts/words.ts for `crumbsOf` and `folderHref`.
 */
import type { ReactElement } from 'react';
import { crumbsOf, folderHref } from './words';

export function Breadcrumbs(props: { path: string; parent: string | null }): ReactElement | null {
  const crumbs = crumbsOf(props.path);
  const last = crumbs.length - 1;
  // At the root the one crumb would only repeat the title above it.
  if (last === 0) return null;
  return (
    <nav className="honey-crumbs" aria-label="Honey folder">
      <ol>
        {crumbs.map((crumb, index) => (
          <li key={crumb.path}>
            {index === last ? (
              <span aria-current="page">{crumb.name}</span>
            ) : (
              <a href={folderHref(crumb.path)}>{crumb.name}</a>
            )}
          </li>
        ))}
      </ol>
      {props.parent !== null ? <a href={folderHref(props.parent)}>Up</a> : null}
    </nav>
  );
}
