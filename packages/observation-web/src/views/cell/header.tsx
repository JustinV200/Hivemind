/**
 * `CellHeader`: the Cell page's header, with the tier and mask badges that are never hidden.
 *
 * The header leads with the Cell's name, as the Fleet rows and the Forage's host cards do, with
 * the full id beside it as the subject, then its kind, source and headline status, then the
 * `CombShieldLevel` badge and the Pheromone Mask badge, then the access level and mode. The two badges are
 * persistent and large at every width: they are plain elements in the flow of the header, wrap to
 * a second line on a phone rather than collapse, and are not behind a tooltip or a disclosure.
 * The header sticks under the summary strip while the page scrolls. Until the page is read it
 * is titled `Cell` over the id alone, so the route's subject is on screen from the first paint.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cell/`. Rendered by `cell.tsx`; styled by `cell.css`.
 *
 * Key invariants:
 *   - The mask badge shows `OFF` when the page carries no mask state, with a note that the
 *     state is not tracked yet, so a Cell is never shown masked or unmasked on no evidence.
 *   - The tier and mask badges are the shared components, so the pill and tag shapes match the
 *     Fleet rows and the legend.
 *
 * See Also:
 *   - docs/observation/design.md sections 3.3, 4.1 and 5.2; codingrules 8.11.
 */
import type { ReactElement } from 'react';
import { MaskBadge, StatusDot, TierBadge, ViewTitle } from '../../components';
import type { CellPage } from './slice';

function Badges(props: { page: CellPage }): ReactElement {
  const { cell } = props.page;
  return (
    <div className="cell-badges">
      <TierBadge level={cell.comb_shield} />
      <MaskBadge state={cell.mask_state ?? 'OFF'} />
      {cell.mask_state == null ? (
        <span className="cell-muted">mask state not tracked yet</span>
      ) : null}
      <span>{`Access ${cell.access_level.replace('_', ' ')}`}</span>
      <span className="cell-mode">
        Mode{' '}
        {cell.mode === null ? (
          <span className="cell-muted">not reported yet</span>
        ) : (
          <StatusDot word={cell.mode === 'ACTIVE' ? 'active' : 'watch'} />
        )}
      </span>
    </div>
  );
}

export function CellHeader(props: { id: string; page: CellPage | null }): ReactElement {
  const { page } = props;
  return (
    <header className="cell-header">
      <ViewTitle title={page?.cell.name ?? 'Cell'} subject={props.id}>
        {page ? (
          <span className="cell-meta">
            {`${page.cell.kind === 'REAL' ? 'Real' : 'Virtual'} Cell, source ${page.cell.source}, ${page.status}`}
          </span>
        ) : null}
      </ViewTitle>
      {page ? <Badges page={page} /> : null}
    </header>
  );
}
