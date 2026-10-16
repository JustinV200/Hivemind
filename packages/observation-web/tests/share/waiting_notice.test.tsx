/**
 * Tests for the ongoing "a question is waiting" notification.
 *
 * ADR-0050: while a session is active the app follows the push stream, keeps one ongoing
 * notification up for as long as a question waits, and withdraws it when the question is answered
 * anywhere. Questions already waiting when the app opens count too.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/share/use_waiting.ts through `PhoneHost`.
 *
 * Key invariants:
 *   - The notifier is called only when the count changes.
 *   - Nothing runs, and the notification is cleared, when the session ends or the host unmounts.
 *
 * See Also:
 *   - docs/entrance/landing-board.md section 9, tests/share/media.test.ts for the reducer.
 */
import { act, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { flush } from '../support/fake_browser';
import type { FakeSocket } from '../support/fake_browser';
import { inboxView, questionView } from '../views/chat/support';
import { FakeNotifier, openPhone } from './support';

const notice = (kind: string, ref: string, id: string) => ({
  kind,
  ref,
  event_id: id,
  created_at: '2026-01-01T00:00:00Z',
});

async function deliver(socket: FakeSocket, frame: unknown): Promise<void> {
  await act(async () => {
    socket.receive(frame);
    await flush();
  });
}

const openStream = (notifier: FakeNotifier, inbox = inboxView()) => openPhone({ notifier, inbox });

describe('waiting notification', () => {
  it('follows the push stream: shown while a question waits, cleared when answered anywhere', async () => {
    const notifier = new FakeNotifier();
    const phone = await openStream(notifier);
    await waitFor(() => expect(phone.fake.sockets).toHaveLength(1));
    const socket = phone.fake.sockets[0]!;
    expect(socket.url).toContain('/v1/push/stream');
    await act(async () => {
      socket.open();
      await flush();
    });
    await deliver(socket, notice('question_waiting', 'q1', 'e1'));
    await deliver(socket, notice('question_waiting', 'q2', 'e2'));
    await deliver(socket, notice('question_waiting', 'q2', 'e2'));
    await deliver(socket, notice('alarm_waiting', 'a1', 'e3'));
    await deliver(socket, notice('withdrawn', 'q1', 'e4'));
    await deliver(socket, notice('withdrawn', 'q2', 'e5'));
    expect(notifier.calls).toEqual(['show 1', 'show 2', 'show 1', 'clear']);
  });

  it('counts the questions already waiting when the app opens', async () => {
    const notifier = new FakeNotifier();
    await openStream(notifier, inboxView({ questions: [questionView()] }));
    await waitFor(() => expect(notifier.calls).toEqual(['show 1']));
  });

  it('re-reads the inbox when the stream comes back, so a notice missed meanwhile is caught up', async () => {
    const notifier = new FakeNotifier();
    const phone = await openStream(notifier);
    await waitFor(() => expect(phone.fake.sockets).toHaveLength(1));
    const first = phone.fake.sockets[0]!;
    await act(async () => {
      first.open();
      await flush();
    });
    await deliver(first, notice('question_waiting', 'q1', 'e1'));
    expect(notifier.calls).toEqual(['show 1']);
    // The phone loses the network; q1 is answered from the laptop meanwhile, and the push stream
    // keeps no replay, so the `withdrawn` for this device is gone for good.
    await act(async () => {
      first.serverClose(1006);
      phone.fake.advance(1000);
      await flush();
    });
    const second = phone.fake.sockets[1]!;
    await act(async () => {
      second.open();
      await flush();
    });
    // Back live: the inbox is the truth, and says nothing waits.
    await deliver(second, notice('alarm_waiting', 'a1', 'e2'));
    await waitFor(() => expect(notifier.calls).toEqual(['show 1', 'clear']));
  });

  it('does not re-add a question withdrawn while the inbox read was out', async () => {
    const notifier = new FakeNotifier();
    const phone = await openPhone({ notifier, inbox: inboxView({ questions: [questionView()] }) });
    await waitFor(() => expect(notifier.calls).toEqual(['show 1']));
    const socket = phone.fake.sockets[0]!;
    // The stream goes live (which starts a fresh read) and the withdrawal arrives before the read
    // has answered; the stale answer must not bring the question back.
    const original = phone.fake.browser.fetch;
    let release = (): void => undefined;
    const held = new Promise<void>((resolve) => (release = resolve));
    phone.fake.browser.fetch = async (url, init) => {
      await held;
      return original(url, init);
    };
    await deliver(socket, notice('withdrawn', questionView().id, 'e9'));
    expect(notifier.calls).toEqual(['show 1', 'clear']);
    release();
    await act(flush);
    expect(notifier.calls).toEqual(['show 1', 'clear']);
  });

  it('clears the notification and closes the stream when the host goes away', async () => {
    const notifier = new FakeNotifier();
    const phone = await openStream(notifier);
    await waitFor(() => expect(phone.fake.sockets).toHaveLength(1));
    phone.view.unmount();
    expect(phone.fake.sockets[0]!.closedWith).toBe(1000);
    expect(notifier.calls.at(-1)).toBe('clear');
  });

  it('does nothing without a session', async () => {
    const notifier = new FakeNotifier();
    const phone = await openPhone({ notifier, signedIn: false });
    expect(phone.fake.sockets).toHaveLength(0);
    expect(notifier.calls).toEqual([]);
  });
});
