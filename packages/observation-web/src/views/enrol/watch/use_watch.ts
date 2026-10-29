/**
 * `useWatch()`: read the tasks waiting for the owner's yes to use the running screen or audio.
 *
 * ADR-0069: a task that would drive the operator's own screen, or the sound server, is held by the
 * Queen until the owner answers on the Hive Stand. This hook reads `GET /v1/entrance/watch` on
 * mount and whenever `again()` is called (after an answer, or the owner's "Check again"), and keeps
 * the list and any refusal in local state. The answers themselves are `actions.answerWatch`.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/enrol/watch/`. Used by `enrol_screen.tsx`, which is
 *   mounted on the Hive Stand only; reads through the Landing Board client from the store's
 *   provider.
 *
 * Key invariants:
 *   - Only `PENDING` requests are kept; one the Queen already settled is dropped on the next read.
 *   - A failed read is kept as `error` and shown through `ErrorNotice`, never an empty list.
 *
 * See Also:
 *   - src/views/enrol/watch/watch_list.tsx for how a request is drawn.
 */
import { useCallback, useEffect, useState } from 'react';
import type { components } from '../../../landing_board/types';
import { useHive } from '../../../store';

export type WatchRequest = components['schemas']['WatchRequestView'];

export interface WatchState {
  seeded: boolean;
  requests: readonly WatchRequest[];
  error: unknown;
  again(): void;
}

export function useWatch(): WatchState {
  const { client } = useHive();
  const [requests, setRequests] = useState<readonly WatchRequest[]>([]);
  const [seeded, setSeeded] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const again = useCallback(() => {
    client.http
      .call('get', '/v1/entrance/watch')
      .then((list) => {
        setRequests(list.requests.filter((r) => r.status === 'PENDING'));
        setSeeded(true);
        setError(null);
      })
      .catch((failure: unknown) => setError(failure));
  }, [client]);

  useEffect(again, [again]);
  return { seeded, requests, error, again };
}
