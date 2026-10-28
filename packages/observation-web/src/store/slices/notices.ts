/**
 * The notices slice: what the push stream says is waiting, for the shell's notice bar.
 *
 * `/v1/push/stream` carries content-free `PushNotice`s: a question or an Alarm is waiting, the
 * Queen replied, a goal finished, a security event happened, or an earlier notice is withdrawn.
 * The shell keeps the ones not yet seen to, so a question that arrives while another view is open
 * is surfaced there (design.md 5.6). Each `event_id` is folded at most once, as the contract asks,
 * a `withdrawn` removes every notice with the same `ref`, and the human can dismiss the rest.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/store/slices/`. Folded by `HiveStore`; fed by `feed.ts` from
 *   the push stream; read by `src/shell/notice_bar.tsx`.
 *
 * Key invariants:
 *   - Pure and bounded: at most `MAX_NOTICES` are kept, oldest dropped first, and the seen ids
 *     are bounded the same way.
 *   - Never carries the item itself: only its kind, its ref and when it was minted.
 *
 * See Also:
 *   - docs/entrance/landing-board.md section 9 for the notice contract.
 *   - src/share/waiting/waiting.ts, the Android counterpart that counts waiting questions.
 */
import type { components } from '../../landing_board/types';

export type PushNotice = components['schemas']['PushNotice'];
export type NoticeKind = components['schemas']['NoticeKind'];

export const MAX_NOTICES = 20; // A bar, not a log: the newest twenty are plenty.
const MAX_SEEN = 200; // Enough ids to catch a retry; older ones cannot be retried any more.

export interface Notice {
  eventId: string;
  kind: NoticeKind;
  ref: string;
  at: string;
}

export interface NoticesSlice {
  items: readonly Notice[];
  seen: readonly string[];
}

export type NoticesEvent =
  | { type: 'notice'; notice: PushNotice }
  | { type: 'dismiss'; eventId: string }
  | { type: 'dismiss_kinds'; kinds: readonly NoticeKind[] };

export const initialNotices: NoticesSlice = { items: [], seen: [] };

function fold(slice: NoticesSlice, notice: PushNotice): NoticesSlice {
  // A retry or a second channel carries the same event id: it changes nothing.
  if (slice.seen.includes(notice.event_id)) return slice;
  const seen = [...slice.seen, notice.event_id].slice(-MAX_SEEN);
  // A withdrawal takes every notice about the same item off the bar.
  if (notice.kind === 'withdrawn') {
    return { seen, items: slice.items.filter((item) => item.ref !== notice.ref) };
  }
  const item: Notice = {
    eventId: notice.event_id,
    kind: notice.kind,
    ref: notice.ref,
    at: notice.created_at,
  };
  return { seen, items: [...slice.items, item].slice(-MAX_NOTICES) };
}

export function reduceNotices(slice: NoticesSlice, event: NoticesEvent): NoticesSlice {
  switch (event.type) {
    case 'notice':
      return fold(slice, event.notice);
    case 'dismiss':
      return { ...slice, items: slice.items.filter((item) => item.eventId !== event.eventId) };
    case 'dismiss_kinds':
      return { ...slice, items: slice.items.filter((item) => !event.kinds.includes(item.kind)) };
  }
}
