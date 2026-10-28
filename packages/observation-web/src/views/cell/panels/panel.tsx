/**
 * `Panel`, `Tag` and `NotTracked`: the three small pieces every Cell page panel is built from.
 *
 * A panel is a labelled section with a heading, so a screen reader can jump between them and the
 * page reads as a list of facts rather than a wall. A tag is a short word in a tone (the shared
 * status tag of components.css); the word is the content and the tone only echoes it, so colour
 * is never the only signal. `NotTracked` is
 * how a field the schema documents as "null until a later phase" is shown: as words, in place of
 * the row, never by dropping the row.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cell/panels/`. Used by every panel beside the diagram;
 *   styled by `src/views/cell/cell.css`.
 *
 * Key invariants:
 *   - The panel's `<section>` is named by its own `<h2>`.
 *   - Tones are the four status tokens' names; the tag carries its text whatever the tone.
 *
 * See Also:
 *   - docs/observation/design.md sections 3.2 and 8 (empty states say what would fill them).
 */
import type { ReactElement, ReactNode } from 'react';

export type TagTone = 'ok' | 'warn' | 'alarm' | 'idle';

export function Panel(props: { id: string; title: string; children: ReactNode }): ReactElement {
  const heading = `cell-panel-${props.id}`;
  return (
    <section className="cell-panel comb-cell" aria-labelledby={heading}>
      <h2 id={heading} className="section-heading">
        {props.title}
      </h2>
      {props.children}
    </section>
  );
}

export function Tag(props: { tone: TagTone; children: ReactNode }): ReactElement {
  return <span className={`tag tag--status tag--${props.tone}`}>{props.children}</span>;
}

export function NotTracked(): ReactElement {
  return <span className="cell-muted">not tracked yet</span>;
}
