/**
 * The Honey browser: the Hive's knowledge as a folder tree, read-only, at `#/honey/<path>`.
 *
 * The screen reads one folder at a time (the root, `/hive`, `/cells/<id>` with its live Cell Wax
 * under `/wax`, `/bees/<id>`, `/tasks/<id>`, `/bee-bread`) exactly as the Entrance cut it for this
 * device: a folder the device may not see is absent from its parent, and opening it by address
 * shows the refusal sentence. Each item shows its clearance and provenance; its title links to
 * the folder's address with `?item=<id>`, which opens it in the reading pane, so an open item can
 * be shared, comes back on reload and closes with Back; a searchable folder has a search box. The Honey Store has no stream, so the
 * folder is read on navigation and again, once per burst, when the trail shows a Honey event for it.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/honey/`. Chosen by the outlet in `src/shell/` when the
 *   route is `honey`. Built to docs/observation/design.md section 5.7.
 *
 * Key invariants:
 *   - Read-only: only GET requests are made, and there is no propose control.
 *   - A folder left over from another path is never shown: it must match the route's path.
 *   - Everything drawn comes from the generated types; the route's path is normalised to the form
 *     the Entrance uses (a leading slash).
 *
 * See Also:
 *   - src/views/honey/README.md.
 *   - src/views/honey/hooks/ for the reads and src/views/honey/slice/ for the state.
 */
import type { ReactElement } from 'react';
import { ErrorNotice, VIEW_HINTS, ViewTitle } from '../../components';
import { useHoneyFolder } from './hooks/use_folder';
import { useReading } from './hooks/use_reading';
import { useSearch } from './hooks/use_search';
import './honey.css';
import { FolderPanel } from './panels/folder_panel';
import { ReadingPane } from './panels/reading_pane';
import { SearchPanel } from './panels/search_panel';
import { Breadcrumbs } from './parts/breadcrumbs';
import { ClearanceLegend } from './parts/clearance_badge';
import { folderHref, normalisePath } from './parts/words';

export function HoneyScreen(props: { path: string; item: string | null }): ReactElement {
  const path = normalisePath(props.path);
  const { item } = props;
  const { state, loadingMore, loadMore } = useHoneyFolder(path);
  // The address says which item the reading pane holds.
  useReading(path, item);
  // A render between a route change and the slice opening the new folder must not show the old one.
  const mine = state.path === path;
  const search = useSearch(path, mine ? state.search : null);
  const folder = mine ? state.folder : null;
  const error = mine ? state.error : null;
  const openId = mine ? (state.reading?.id ?? null) : null;
  return (
    <>
      {/* At the root the path is a lone slash, which says nothing beside the title; the
          crumbs name the folder, so the subject is only shown once there is one */}
      <ViewTitle title="Honey" subject={path === '/' ? undefined : path} hint={VIEW_HINTS.honey}>
        <ClearanceLegend />
      </ViewTitle>
      <Breadcrumbs path={path} parent={folder?.parent ?? null} />
      {error ? (
        <>
          <ErrorNotice error={error} />
          <p>
            <a href={folderHref('/')}>Back to the top of the Honey</a>
          </p>
        </>
      ) : null}
      {!folder && !error ? <p aria-busy="true">Reading the folder</p> : null}
      {folder ? (
        <div className={openId !== null ? 'honey honey--reading' : 'honey'}>
          <div className="honey-list">
            {folder.can_search ? (
              <SearchPanel search={mine ? state.search : null} controls={search} path={path} />
            ) : null}
            <FolderPanel
              folder={folder}
              truncated={state.truncated}
              loadingMore={loadingMore}
              openId={openId}
              searching={mine && state.search !== null}
              onMore={loadMore}
            />
          </div>
          {mine && state.reading ? (
            <ReadingPane reading={state.reading} closeHref={folderHref(path)} />
          ) : null}
        </div>
      ) : null}
    </>
  );
}
