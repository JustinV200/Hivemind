/**
 * `useFleetSeed()`: read the fleet once, for a view that needs the Cells' names when no view has
 * seeded the Fleet slice yet.
 *
 * The Fleet slice is what names a Cell and lists the bees on it, and only the Fleet view keeps it
 * live. A view that opens first (the Forage's host cards, the Thoughts' bee picker) still wants
 * those names on its first paint, so it asks here: one `GET /v1/fleet` when the slice is not
 * seeded, and nothing when it is. A failure is swallowed, because the names are a nicety; the
 * Fleet view reports the failure itself when it is opened.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/fleet/`. Used by `src/views/forage/use_forage.ts` and
 *   `src/views/thoughts/bee_nav.tsx`; writes only the `fleet` slice's seed.
 *
 * Key invariants:
 *   - At most one read per mount, and none when the fleet has been read already.
 *   - An answer that lands after the reader left is dropped.
 *
 * See Also:
 *   - src/views/fleet/use_fleet.ts, the Fleet view's own reader, which also re-reads on frames.
 */
import { useEffect } from 'react';
import { useHive, useSlice } from '../../store';

export function useFleetSeed(): void {
  const { client, store } = useHive();
  const fleet = useSlice('fleet');
  useEffect(() => {
    if (fleet.seeded) return;
    let current = true;
    client.http
      .call('get', '/v1/fleet')
      .then((answer) => {
        if (current) store.dispatch({ slice: 'fleet', event: { type: 'seeded', answer } });
      })
      .catch(() => {
        // Names are a nicety: the view shows ids when the fleet cannot be read, and the Fleet
        // view will report the failure itself when it is opened.
      });
    return () => {
      current = false;
    };
  }, [client, store, fleet.seeded]);
}
