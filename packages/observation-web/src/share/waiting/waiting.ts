/**
 * Which questions are waiting, worked out from the push stream's content-free notices.
 *
 * `/v1/push/stream` says only that something is waiting (`question_waiting` with the question's
 * `ref`) and later that it is no longer (`withdrawn` with the same `ref`). This reducer keeps the set
 * of waiting question refs, processing each `event_id` at most once as the contract asks
 * (landing-board.md section 9). It is pure; `use_waiting.ts` feeds it and acts on the count.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/share/`. Used by `use_waiting.ts`.
 *
 * Key invariants:
 *   - Idempotent by `event_id`: a repeat changes nothing.
 *   - Only questions are counted (an Alarm or a reply is not "a question is waiting").
 *   - A `withdrawn` for a ref that was never seen is harmless.
 *
 * See Also:
 *   - docs/entrance/landing-board.md section 9, and docs/adr/0047 for the ongoing notification.
 */
import type { components } from '../../landing_board/types';

export type PushNotice = components['schemas']['PushNotice'];

export interface WaitingState {
  refs: ReadonlySet<string>;
  seen: ReadonlySet<string>;
}

export const NOTHING_WAITING: WaitingState = { refs: new Set(), seen: new Set() };

// Questions already waiting when the stream opens, from the inbox read.
export function seeded(refs: readonly string[]): WaitingState {
  return { refs: new Set(refs), seen: new Set() };
}

export function applyNotice(state: WaitingState, notice: PushNotice): WaitingState {
  if (state.seen.has(notice.event_id)) return state;
  const seen = new Set(state.seen).add(notice.event_id);
  const refs = new Set(state.refs);
  if (notice.kind === 'question_waiting') refs.add(notice.ref);
  else if (notice.kind === 'withdrawn') refs.delete(notice.ref);
  return { refs, seen };
}
