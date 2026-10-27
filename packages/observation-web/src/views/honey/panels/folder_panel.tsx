/**
 * `FolderPanel`: the open folder's ceiling, note, sub-folders and items, with a way to page on.
 *
 * Under the folder's header (the most sensitive label this viewer may read, and the Entrance's
 * own note when the listing is not what one would expect) it lists the sub-folders the viewer's
 * scopes reach and then a page of items, each with its clearance and provenance. "More" reads the
 * next page by cursor. A last line says that proposing a note is not available here yet: the
 * Observation Hive has no path into the House Bee's queue, and it never says "saved".
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/honey/panels/`. Drawn by the Honey screen from the
 *   slice's folder; it reads nothing itself.
 *
 * Key invariants:
 *   - Draws only the folder as the Entrance cut it for this viewer; nothing is added or filtered.
 *   - Nothing here writes: no propose control exists, only the one-line note.
 *   - An empty folder says so, so an empty screen is never mistaken for a loading one.
 *
 * See Also:
 *   - docs/observation/design.md section 5.7.
 */
import type { ReactElement } from 'react';
import { EmptyState } from '../../../components';
import { ClearanceBadge } from '../parts/clearance_badge';
import { ItemRow } from '../parts/item_row';
import { folderHref } from '../parts/words';
import type { HoneyFolder } from '../slice';

export const NO_PROPOSALS = 'Proposing a note is not available from the Observation Hive yet.';

function SubFolders(props: { folders: HoneyFolder['folders'] }): ReactElement | null {
  if (props.folders.length === 0) return null;
  return (
    <section className="honey-group" aria-labelledby="honey-folders-title">
      <h2 id="honey-folders-title" className="section-heading">
        Folders
      </h2>
      <ul className="honey-folders">
        {props.folders.map((folder) => (
          <li key={folder.path}>
            <a href={folderHref(folder.path)}>{folder.title}</a>
            <code>{folder.name}</code>
            {folder.detail ? <span className="honey-muted">{folder.detail}</span> : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

export function FolderPanel(props: {
  folder: HoneyFolder;
  truncated: boolean;
  loadingMore: boolean;
  openId: string | null;
  onMore: () => void;
  onOpen: (id: string) => void;
}): ReactElement {
  const { folder, truncated, loadingMore, openId, onMore, onOpen } = props;
  const empty = folder.folders.length === 0 && folder.items.length === 0;
  return (
    <>
      <p className="honey-ceiling">
        You may read up to <ClearanceBadge label={folder.ceiling} />
      </p>
      {folder.note ? <p className="honey-note">{folder.note}</p> : null}
      <SubFolders folders={folder.folders} />
      {folder.items.length > 0 ? (
        <section className="honey-group" aria-labelledby="honey-items-title">
          <h2 id="honey-items-title" className="section-heading">
            Items
          </h2>
          <ul className="honey-items">
            {folder.items.map((item) => (
              <ItemRow key={item.id} item={item} open={item.id === openId} onOpen={onOpen} />
            ))}
          </ul>
        </section>
      ) : null}
      {empty ? <EmptyState>Nothing in this folder that this device may see.</EmptyState> : null}
      {folder.next_cursor !== null ? (
        <button type="button" className="button" disabled={loadingMore} onClick={onMore}>
          More
        </button>
      ) : null}
      {truncated ? (
        <p className="honey-muted">The list is cut here. Search the folder to find the rest.</p>
      ) : null}
      <p className="honey-muted">{NO_PROPOSALS}</p>
    </>
  );
}
