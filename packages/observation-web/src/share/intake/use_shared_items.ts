/**
 * `useSharedItems()`: the items other apps have shared and the human has not yet decided on.
 *
 * Subscribes to the platform's share sheet for as long as the host is mounted and queues each item
 * in order. The queue is React state, so it lives in memory only: an item shared while the human is
 * logged out simply waits here until login, and it is gone if the app is closed. Nothing is stored
 * on disk, in IndexedDB or in a cookie (ADR-0047).
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/share/`. Used by `phone_host.tsx`.
 *
 * Key invariants:
 *   - The first item in the queue is the one asked about; `finish` removes exactly that item.
 *   - Subscribing happens once per mount and the cleanup stops it.
 *
 * See Also:
 *   - src/landing_board/client/platform/android/share.ts for where items come from.
 */
import { useCallback, useEffect, useState } from 'react';
import type { ShareIntake, SharedItem } from '../../landing_board/client';

export interface SharedItems {
  current: SharedItem | null;
  finish(): void;
}

export function useSharedItems(intake: ShareIntake): SharedItems {
  const [queue, setQueue] = useState<readonly SharedItem[]>([]);
  useEffect(() => intake.onShared((item) => setQueue((now) => [...now, item])), [intake]);
  const finish = useCallback(() => setQueue((now) => now.slice(1)), []);
  return { current: queue[0] ?? null, finish };
}
