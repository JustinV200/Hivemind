/**
 * The Help screen: the help pages, a search over them and the tour's start, at `#/help/<page>`.
 *
 * The screen lists the pages (start here, the words, how the Hive works by theme, the screens)
 * and draws the open one from its markdown. A search box in the title row searches every page as
 * the person types, since the text is bundled and the search costs nothing; the hits, each a
 * section with its words, stand in place of the page until the box is cleared. A link into a
 * section (`?s=<heading id>`, what another screen's "More in Help" and the legends carry) lands
 * focus on that heading. Beside the pages, the tour's card starts the guided tour and says
 * whether this browser has finished it.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/help/`. Chosen by the outlet in `src/shell/` when the
 *   route is `help`. Reads nothing from the Hive: every word is bundled.
 *
 * Key invariants:
 *   - No request is made and no slice is read; the screen is the same on every device.
 *   - An unknown page id shows the page list with "not found" wording, never a blank screen.
 *   - The page's `h1` is the view title; the content's headings start at `h2`.
 *
 * See Also:
 *   - docs/observation/design.md section 5.12; src/views/help/README.md.
 */
import { useEffect, useState } from 'react';
import type { ReactElement } from 'react';
import { EmptyState, VIEW_HINTS, ViewTitle } from '../../components';
import { hrefFor } from '../../router';
import './help.css';
import { HELP_PAGES, helpPage } from './pages';
import type { HelpPage } from './pages';
import { headingId, Markdown } from './render';
import { searchHelp } from './search';
import { TourCard } from './tour';

// The page list: every page, the open one marked current in words and weight, never colour alone.
function PageList(props: { current: string | null }): ReactElement {
  return (
    <nav className="help-pages comb-cell" aria-label="Help pages">
      <ul>
        {HELP_PAGES.map((page) => (
          <li key={page.id}>
            <a
              href={hrefFor({ name: 'help', page: page.id, section: null })}
              aria-current={page.id === props.current ? 'page' : undefined}
            >
              {page.title}
            </a>
            <span className="help-summary">{page.summary}</span>
          </li>
        ))}
      </ul>
    </nav>
  );
}

// The hits for the typed words, each heading a link into its page, its text drawn under it.
function Results(props: { query: string }): ReactElement {
  const hits = searchHelp(props.query);
  return (
    <section className="help-page comb-cell" aria-labelledby="help-results-title">
      <h2 id="help-results-title" className="section-heading">
        {`Results for "${props.query.trim()}" (${hits.length})`}
      </h2>
      {hits.length === 0 ? <p>{`No matches for "${props.query.trim()}".`}</p> : null}
      {hits.map((hit) => (
        <article key={`${hit.page.id}/${hit.id}`} className="help-hit">
          <p className="help-hit-page">
            <a href={hrefFor({ name: 'help', page: hit.page.id, section: hit.id })}>
              {hit.page.title}
            </a>
          </p>
          {/* The section's own heading opens its block list, so the hit reads as on its page. */}
          <Markdown blocks={hit.blocks} />
        </article>
      ))}
    </section>
  );
}

// Lands focus on the section the address names once the page is drawn; the sticky head is kept
// clear of it by the heading's scroll margin in help.css.
function useSectionFocus(page: HelpPage | null, section: string | null): void {
  useEffect(() => {
    if (page === null || section === null) return;
    const heading = document.getElementById(headingId(section));
    if (heading === null) return;
    // jsdom has no scrollIntoView; a browser scrolls, with motion only where it is welcome.
    if (typeof heading.scrollIntoView === 'function') {
      const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
      heading.scrollIntoView({ block: 'start', behavior: reduce ? 'auto' : 'smooth' });
    }
    heading.focus({ preventScroll: true });
  }, [page, section]);
}

export function HelpScreen(props: { page: string | null; section: string | null }): ReactElement {
  const page = helpPage(props.page);
  const [query, setQuery] = useState('');
  const searching = query.trim() !== '';
  useSectionFocus(page, props.section);

  return (
    <>
      <ViewTitle title="Help" hint={VIEW_HINTS.help} help="help">
        <form className="help-search" role="search" onSubmit={(event) => event.preventDefault()}>
          <label htmlFor="help-q">Search the help</label>
          <input
            id="help-q"
            type="search"
            value={query}
            placeholder="a word or a phrase"
            onChange={(event) => setQuery(event.target.value)}
          />
          {searching ? (
            <button type="button" className="button" onClick={() => setQuery('')}>
              Clear
            </button>
          ) : null}
        </form>
      </ViewTitle>
      <div className="help">
        <div className="help-side">
          <PageList current={page?.id ?? null} />
          <TourCard />
        </div>
        {searching ? (
          <Results query={query} />
        ) : page ? (
          <article className="help-page comb-cell" aria-labelledby="help-page-title">
            <h2 id="help-page-title" className="section-heading">
              {page.title}
            </h2>
            <Markdown blocks={page.blocks} />
          </article>
        ) : (
          <EmptyState>There is no help page at this address. Pick one from the list.</EmptyState>
        )}
      </div>
    </>
  );
}
