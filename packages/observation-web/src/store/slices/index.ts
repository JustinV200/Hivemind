/**
 * The built-in slices as a table: the session, the connection, the summary and the notices.
 *
 * `HiveStore` is built from a table of slices. These four belong to the shell (login, the top bar,
 * the strip and the notice bar), so they travel together as `SHELL_SLICES`; the composition root
 * spreads it into the app's table beside each view's slice.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/store/slices/`. Spread by `src/app.tsx`; the modules beside
 *   it hold each slice's state, events and reducer.
 *
 * Key invariants:
 *   - Re-exports and the one table only; no reducer logic lives here.
 *
 * See Also:
 *   - src/store/registry.ts for the type that lists these by name.
 */
import type { SliceRegistry } from '../registry';
import { initialConnection, reduceConnection } from './connection';
import { initialNotices, reduceNotices } from './notices';
import { initialSession, reduceSession } from './session';
import { initialSummary, reduceSummary } from './summary';

export const SHELL_SLICES = {
  session: { initial: initialSession, reduce: reduceSession },
  connection: { initial: initialConnection, reduce: reduceConnection },
  summary: { initial: initialSummary, reduce: reduceSummary },
  notices: { initial: initialNotices, reduce: reduceNotices },
} satisfies Pick<SliceRegistry, 'session' | 'connection' | 'summary' | 'notices'>;
