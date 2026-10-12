/**
 * `Fact`: one labelled fact in a line of an item's details, such as `Task 1a2b3c4d`.
 *
 * Provenance and the wax fields are both lines of short facts. Each is a plain-text label followed
 * by a value the caller draws, so a screen reader hears "Task" before the id and a sighted reader
 * can scan the labels. A fact with nothing to say is left out by the caller, never drawn blank.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/honey/parts/`. Used by `provenance.tsx` and
 *   `wax_facts.tsx`; styled by `honey.css`.
 *
 * Key invariants:
 *   - The label is real text, not an attribute or a pseudo-element.
 *   - Holds no state and reads no slice.
 *
 * See Also:
 *   - src/views/honey/honey.css for `.honey-fact`.
 */
import type { ReactElement, ReactNode } from 'react';

export function Fact(props: { label: string; children: ReactNode }): ReactElement {
  return (
    <span className="honey-fact">
      <span className="honey-fact-label">{props.label}</span> {props.children}
    </span>
  );
}
