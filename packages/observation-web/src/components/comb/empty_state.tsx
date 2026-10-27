/**
 * `EmptyState`: the sentence that says what would fill a view, on a faint comb.
 *
 * Design section 8 asks every empty view to say what would fill it, never to show a blank. The
 * words are the content; the comb behind them (`HexPattern`) says the frame is ready and waiting,
 * which is one of the few places the honeycomb may appear.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/components/comb/`. Used by the views in place of a bare `<p>`.
 *
 * Key invariants:
 *   - The children are rendered inside a `<p>`, so the sentence stays a paragraph for readers.
 *   - No state; the pattern is decorative and hidden from assistive technology.
 *
 * See Also:
 *   - docs/observation/design.md section 8 (empty states).
 */
import type { ReactElement, ReactNode } from 'react';
import { HexPattern } from './hex_pattern';

export function EmptyState(props: { children: ReactNode }): ReactElement {
  return (
    <div className="empty">
      <HexPattern />
      <p>{props.children}</p>
    </div>
  );
}
