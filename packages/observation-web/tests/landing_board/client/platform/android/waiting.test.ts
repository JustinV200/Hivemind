/**
 * Tests for the waiting notification adapter.
 *
 * The ongoing local notification says only that a question is waiting. It asks for permission once
 * (silently giving up if refused), reuses one id so questions do not stack, is ongoing, and is
 * cancelled by id.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises platform/android/waiting.ts.
 *
 * Key invariants:
 *   - A refused permission schedules nothing and does not throw.
 *   - The notification never names the question.
 *
 * See Also:
 *   - docs/adr/0047, "Push is UnifiedPush, not FCM" (the foreground fallback).
 */
import { describe, expect, it } from 'vitest';
import { androidWaitingNotifier } from '../../../../../src/landing_board/client/platform/android/waiting';
import type { NotificationsLike } from '../../../../../src/landing_board/client/platform/android/waiting';

type Scheduled = Parameters<NotificationsLike['schedule']>[0];
type Cancelled = Parameters<NotificationsLike['cancel']>[0];

function fakeNotifications(initial: 'granted' | 'prompt', afterAsk: 'granted' | 'denied') {
  const scheduled: Scheduled[] = [];
  const cancelled: Cancelled[] = [];
  let asked = 0;
  const plugin = {
    checkPermissions: () => Promise.resolve({ display: initial }),
    requestPermissions: () => {
      asked += 1;
      return Promise.resolve({ display: afterAsk });
    },
    schedule: (options: Scheduled) => {
      scheduled.push(options);
      return Promise.resolve({ notifications: [] });
    },
    cancel: (options: Cancelled) => {
      cancelled.push(options);
      return Promise.resolve();
    },
  } as NotificationsLike;
  return { plugin, scheduled, cancelled, asked: () => asked };
}

describe('android waiting notifier', () => {
  it('posts one ongoing notification that names no question', async () => {
    const fake = fakeNotifications('granted', 'granted');
    const notifier = androidWaitingNotifier(fake.plugin);
    await notifier.show(1);
    await notifier.show(3);
    const [first, second] = fake.scheduled.map((s) => s.notifications[0]!);
    expect(first).toMatchObject({ ongoing: true, autoCancel: false, body: 'A question is waiting.' });
    expect(second?.body).toBe('3 questions are waiting.');
    expect(second?.id).toBe(first?.id);
    expect(fake.asked()).toBe(0);
  });

  it('asks for permission when it is not granted yet, and posts once it is', async () => {
    const fake = fakeNotifications('prompt', 'granted');
    await androidWaitingNotifier(fake.plugin).show(1);
    expect(fake.asked()).toBe(1);
    expect(fake.scheduled).toHaveLength(1);
  });

  it('posts nothing when the human refuses permission', async () => {
    const fake = fakeNotifications('prompt', 'denied');
    await expect(androidWaitingNotifier(fake.plugin).show(1)).resolves.toBeUndefined();
    expect(fake.scheduled).toHaveLength(0);
  });

  it('cancels its notification by the id it posted under', async () => {
    const fake = fakeNotifications('granted', 'granted');
    const notifier = androidWaitingNotifier(fake.plugin);
    await notifier.show(1);
    await notifier.clear();
    expect(fake.cancelled[0]?.notifications[0]?.id).toBe(fake.scheduled[0]?.notifications[0]?.id);
  });
});
