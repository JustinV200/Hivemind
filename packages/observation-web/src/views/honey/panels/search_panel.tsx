/**
 * `SearchPanel`: a search box for the open folder, and the ranked hits it finds.
 *
 * The box is drawn only for a folder the Entrance says can be searched. A search is explicit (a
 * button or Enter), never as-you-type, because it can cost an embedding call. Each hit shows its
 * clearance, title (the button that opens it), score, the passage that matched and provenance; the
 * page's own note (for example that the hits ran out) is shown as the Entrance wrote it.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/honey/panels/`. Drawn by the Honey screen; the reads are
 *   made by `hooks/use_search.ts`, which it receives as props.
 *
 * Key invariants:
 *   - No box and no results for a folder that cannot be searched.
 *   - Every request is a GET made by the hook; this component only calls it.
 *   - A search with nothing found says so, but only after the first page has landed.
 *
 * See Also:
 *   - src/views/honey/hooks/use_search.ts and docs/observation/design.md section 5.7.
 */
import { useState } from 'react';
import type { FormEvent, ReactElement } from 'react';
import { ErrorNotice } from '../../../components';
import { ClearanceBadge } from '../parts/clearance_badge';
import { Provenance } from '../parts/provenance';
import type { SearchHit, SearchState } from '../slice';
import type { Search } from '../hooks/use_search';

// The route refuses a query longer than this many characters.
const MAX_QUERY_CHARS = 2000;

function Hit(props: { hit: SearchHit; onOpen: (id: string) => void }): ReactElement {
  const { hit, onOpen } = props;
  return (
    <li className="honey-item">
      <div className="honey-item-head">
        <ClearanceBadge label={hit.clearance} />
        <button type="button" className="honey-open" onClick={() => onOpen(hit.id)}>
          {hit.title}
        </button>
        <span className="honey-muted">{`Score ${hit.score.toFixed(2)}`}</span>
      </div>
      <p className="honey-snippet">{hit.snippet}</p>
      <Provenance source={hit.provenance} />
    </li>
  );
}

function Results(props: {
  search: SearchState;
  controls: Search;
  onOpen: (id: string) => void;
}): ReactElement {
  const { search, controls, onOpen } = props;
  return (
    <section aria-label="Search results">
      {search.error ? <ErrorNotice error={search.error} /> : null}
      {!search.done ? <p aria-busy="true">Searching</p> : null}
      {search.done && !search.error && search.hits.length === 0 ? <p>No matches.</p> : null}
      <ul className="honey-items">
        {search.hits.map((hit) => (
          <Hit key={hit.id} hit={hit} onOpen={onOpen} />
        ))}
      </ul>
      {search.note ? <p className="honey-note">{search.note}</p> : null}
      {search.nextCursor !== null ? (
        <button
          type="button"
          className="button"
          disabled={controls.loadingMore}
          onClick={controls.more}
        >
          More results
        </button>
      ) : null}
      {search.truncated ? (
        <p className="honey-muted">The results are cut here. Narrow the words.</p>
      ) : null}
    </section>
  );
}

export function SearchPanel(props: {
  search: SearchState | null;
  controls: Search;
  onOpen: (id: string) => void;
}): ReactElement {
  const { search, controls, onOpen } = props;
  const [text, setText] = useState('');
  const submit = (event: FormEvent): void => {
    event.preventDefault();
    controls.submit(text);
  };
  return (
    <section className="honey-search" aria-label="Folder search">
      <form onSubmit={submit} role="search">
        <label htmlFor="honey-q">Search this folder</label>
        <input
          id="honey-q"
          type="search"
          value={text}
          maxLength={MAX_QUERY_CHARS}
          onChange={(event) => setText(event.target.value)}
        />
        <button type="submit" className="button">
          Search
        </button>
        {search ? (
          <button type="button" className="button" onClick={controls.clear}>
            Clear
          </button>
        ) : null}
      </form>
      {search ? <Results search={search} controls={controls} onOpen={onOpen} /> : null}
    </section>
  );
}
