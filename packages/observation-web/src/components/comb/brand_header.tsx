/**
 * `BrandHeader`: the mark and the HiveMind name at the top of a card shown before any shell.
 *
 * The login card and the join screen's cards stand alone on the comb, with no top bar to say whose
 * page it is, so each opens with this one header. Keeping it in one component means the two screens
 * cannot drift apart.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/components/comb/`. Used by `src/views/login/login_form.tsx` and
 *   `src/views/enrol/join/join_screen.tsx`; styled by `.brand-header` in `src/styles/components.css`.
 *
 * Key invariants:
 *   - Decorative mark, readable name: the text is the content, the mark stays `aria-hidden`.
 *
 * See Also:
 *   - src/components/comb/mark.tsx for the mark itself.
 */
import type { ReactElement } from 'react';
import { Mark } from './mark';

export function BrandHeader(): ReactElement {
  return (
    <span className="brand-header">
      <Mark />
      HiveMind
    </span>
  );
}
