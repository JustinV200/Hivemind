/**
 * `ReadingPane`: one item read in full: its label, provenance, summary, content, reason, sources.
 *
 * The pane opens beside the folder from 960px and replaces it at phone width (see `honey.css`).
 * The content is pre-formatted and scrolls inside its own box, so a long note never widens the
 * page. A refusal shows the error table's sentence and nothing about the scope or label that
 * refused it; an item that is gone (404) says so in plain words, since the table has no sentence
 * for it and the Entrance's detail would only repeat an id.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/honey/panels/`. Drawn by the Honey screen from the
 *   slice's reading state, which `hooks/use_reading.ts` fills with one GET per open.
 *
 * Key invariants:
 *   - Draws only what `HoneyItemView` carries; a summary, reason or source list that is null or
 *     empty is left out, not shown blank.
 *   - The clearance tag is always shown with the title.
 *   - Nothing here writes; Close only clears the pane.
 *
 * See Also:
 *   - src/landing_board/client/errors.ts for the sentences, and design.md section 5.7.
 */
import type { ReactElement } from 'react';
import { ErrorNotice } from '../../../components';
import { LandingBoardError } from '../../../landing_board/client';
import { ClearanceBadge } from '../parts/clearance_badge';
import { Provenance } from '../parts/provenance';
import { WaxFacts } from '../parts/wax_facts';
import { kindLabel } from '../parts/words';
import type { ItemView, ReadingState } from '../slice';

export const GONE = 'That item is gone. It may have been retired, or a wax note may have lapsed.';

function Section(props: { title: string; children: ReactElement }): ReactElement {
  return (
    <section>
      <h3 className="honey-section">{props.title}</h3>
      {props.children}
    </section>
  );
}

function Body(props: { item: ItemView }): ReactElement {
  const { item } = props;
  return (
    <article>
      <h2>{item.title}</h2>
      <p className="honey-item-head">
        <ClearanceBadge label={item.clearance} />
        <span className="honey-kind">{kindLabel(item.kind)}</span>
        <code>{item.path}</code>
      </p>
      <WaxFacts item={item} />
      <Provenance source={item.provenance} />
      {item.summary ? (
        <Section title="Summary">
          <p>{item.summary}</p>
        </Section>
      ) : null}
      <Section title="Content">
        <pre className="honey-content" tabIndex={0}>
          {item.content}
        </pre>
      </Section>
      {item.reason ? (
        <Section title="Reason">
          <p>{item.reason}</p>
        </Section>
      ) : null}
      {item.sources.length > 0 ? (
        <Section title="Other sources">
          <ul className="honey-sources">
            {item.sources.map((source, index) => (
              <li key={index}>
                <Provenance source={source} />
              </li>
            ))}
          </ul>
        </Section>
      ) : null}
    </article>
  );
}

export function ReadingPane(props: { reading: ReadingState; onClose: () => void }): ReactElement {
  const { reading, onClose } = props;
  const { item, error } = reading;
  const gone = error instanceof LandingBoardError && error.status === 404;
  return (
    <aside className="honey-pane comb-cell" aria-label="Item">
      <button type="button" className="button" onClick={onClose}>
        Close
      </button>
      {gone ? <p role="alert">{GONE}</p> : null}
      {error && !gone ? <ErrorNotice error={error} /> : null}
      {!item && !error ? <p aria-busy="true">Reading the item</p> : null}
      {item ? <Body item={item} /> : null}
    </aside>
  );
}
