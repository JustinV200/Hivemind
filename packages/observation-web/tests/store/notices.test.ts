/**
 * Tests for the notices reducer: folding push notices once each, withdrawing and dismissing.
 *
 * The notice bar shows what the push stream says is waiting, so the tests pin the contract's
 * rules: an event id folds once however often it is retried, a withdrawal clears every notice
 * about its ref, a dismissal clears a kind, and the list never grows past its bound.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/store/slices/notices.ts.
 *
 * Key invariants:
 *   - Reducers are pure; nothing here reads a clock.
 *
 * See Also:
 *   - docs/entrance/landing-board.md section 9 for the notice contract.
 */
import { describe, expect, it } from 'vitest';
import { initialNotices, MAX_NOTICES, reduceNotices } from '../../src/store/slices/notices';
import type { NoticesEvent, PushNotice } from '../../src/store/slices/notices';

const notice = (over: Partial<PushNotice> = {}): PushNotice => ({
  event_id: 'event_1',
  kind: 'question_waiting',
  ref: 'question_1',
  created_at: '2026-01-01T00:00:00Z',
  ...over,
});

const fold = (events: NoticesEvent[]) => events.reduce(reduceNotices, initialNotices);

describe('reduceNotices', () => {
  it('keeps a notice once per event id, withdraws by ref, and dismisses by kind', () => {
    const once = fold([
      { type: 'notice', notice: notice() },
      { type: 'notice', notice: notice() },
      { type: 'notice', notice: notice({ event_id: 'event_2', kind: 'alarm_waiting', ref: 'a' }) },
    ]);
    expect(once.items.map((item) => item.eventId)).toEqual(['event_1', 'event_2']);
    const withdrawn = reduceNotices(once, {
      type: 'notice',
      notice: notice({ event_id: 'event_3', kind: 'withdrawn', ref: 'question_1' }),
    });
    expect(withdrawn.items.map((item) => item.kind)).toEqual(['alarm_waiting']);
    const dismissed = reduceNotices(withdrawn, { type: 'dismiss_kinds', kinds: ['alarm_waiting'] });
    expect(dismissed.items).toEqual([]);
  });

  it('never holds more than the bound, dropping the oldest', () => {
    const many = Array.from({ length: MAX_NOTICES + 5 }, (_, i) => ({
      type: 'notice' as const,
      notice: notice({ event_id: `event_${i}`, ref: `question_${i}` }),
    }));
    const slice = fold(many);
    expect(slice.items).toHaveLength(MAX_NOTICES);
    expect(slice.items[0]!.eventId).toBe('event_5');
  });
});
