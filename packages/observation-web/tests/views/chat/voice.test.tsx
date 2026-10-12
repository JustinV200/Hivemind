/**
 * Tests for push-to-talk: record on hold, cap, send the exact bytes, show the transcript.
 *
 * The recorder is a fake that counts what the view asks of it and returns a clip the test chose.
 * The rules under test: holding records and releasing posts the clip to `/v1/chat/audio` with the
 * clip's own `Content-Type`, a signature over the hash of those bytes and the intent in the query;
 * the countdown caps the hold at 60 seconds and sends; the transcript is shown, and a spoken goal
 * shows Confirm and Decline before anything runs; a device with no recorder shows no button.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/chat/voice/ through `ChatScreen`.
 *
 * Key invariants:
 *   - Only the interval timers are faked, so the fake browser's crypto and the DOM keep real time.
 *   - The body hash is recomputed here from the recorded request, not read from the view.
 *
 * See Also:
 *   - tests/landing_board/client/http_audio.test.ts for the client method itself.
 */
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { clientError, CLIENT_CODES } from '../../../src/landing_board/client/errors';
import { requestString, sha256Hex } from '../../../src/landing_board/client/signing';
import { flush } from '../../support/fake_browser';
import { verifies } from '../../support/session_fixture';
import { deliver, mountChat } from './mount';
import type { MountedChat } from './mount';
import { chatFrame, goalView, inboxView, questionView, voiceAccepted } from './support';

afterEach(() => {
  vi.useRealTimers();
});

const hold = () => screen.getByRole('button', { name: /Hold to talk/ });

// Presses the button and waits until the recorder has started.
async function press(chat: MountedChat): Promise<void> {
  await screen.findByRole('button', { name: /Hold to talk/ });
  await act(async () => {
    fireEvent.pointerDown(hold());
    await flush();
  });
  expect(chat.recorder!.starts).toBeGreaterThan(0);
}

async function release(): Promise<void> {
  await act(async () => {
    fireEvent.pointerUp(screen.getByRole('button', { name: /Recording/ }));
    await flush();
  });
}

const audioPosts = (chat: MountedChat) =>
  chat.fake.requests.filter((r) => r.target.startsWith('/v1/chat/audio'));

describe('holding the button', () => {
  it('records while held and posts the clip as raw bytes with its own content type', async () => {
    const chat = await mountChat({ recorder: true });
    chat.fake.respond('POST /v1/chat/audio', 202, voiceAccepted());
    await press(chat);
    expect(screen.getByRole('timer').textContent).toBe('60 s left');
    await release();

    await waitFor(() => expect(audioPosts(chat)).toHaveLength(1));
    const request = audioPosts(chat)[0]!;
    expect(request.target).toBe('/v1/chat/audio?intent=goal&duration_s=2.5');
    expect(request.headers['Content-Type']).toBe('audio/webm');
    expect(Array.from(request.body!)).toEqual([1, 2, 3, 4, 5]);
    // The signature covers the hash of exactly those bytes and the target as sent.
    const signed = requestString({
      method: 'POST',
      target: request.target,
      timestamp: Number(request.headers['X-Hive-Timestamp']),
      nonce: request.headers['X-Hive-Nonce']!,
      bodySha256: await sha256Hex(crypto.subtle, request.body!),
    });
    expect(
      await verifies(await publicKeyOf(chat), request.headers['X-Hive-Signature']!, signed),
    ).toBe(true);
    expect(chat.recorder!.stops).toBe(1);
  });

  it('counts down while held and stops and sends at the cap', async () => {
    const chat = await mountChat({ recorder: true });
    chat.fake.respond('POST /v1/chat/audio', 202, voiceAccepted());
    // Only the interval is faked, and only after mounting, because Testing Library polls with it.
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
    await act(async () => {
      fireEvent.pointerDown(hold());
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByRole('timer').textContent).toBe('60 s left');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(screen.getByRole('timer').textContent).toBe('55 s left');
    // Not yet at the cap: nothing has been stopped or sent.
    expect(chat.recorder!.stops).toBe(0);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(55_000);
    });
    expect(chat.recorder!.stops).toBe(1);
    vi.useRealTimers();
    await waitFor(() => expect(audioPosts(chat)).toHaveLength(1));
  });

  it('drops a hold too short to hold speech, with a hint', async () => {
    const chat = await mountChat({ recorder: true });
    chat.recorder!.clip = { ...chat.recorder!.clip, durationS: 0.1 };
    await press(chat);
    await release();
    expect(await screen.findByText('Hold the button while you speak.')).toBeTruthy();
    expect(audioPosts(chat)).toHaveLength(0);
    expect(chat.recorder!.stops).toBe(1);
  });

  it('stops at once when the button is released before the microphone opened', async () => {
    const chat = await mountChat({ recorder: true });
    let open!: () => void;
    chat.recorder!.gate = new Promise((resolve) => (open = resolve));
    await screen.findByRole('button', { name: /Hold to talk/ });
    await act(async () => {
      const button = hold();
      fireEvent.pointerDown(button);
      fireEvent.pointerUp(button);
      open();
      await flush();
    });
    // The recording that opened late was ended, not left running.
    await waitFor(() => expect(chat.recorder!.stops).toBe(1));
  });

  it('shows the refusal when the microphone cannot be used', async () => {
    const chat = await mountChat({ recorder: true });
    chat.recorder!.failWith = clientError(CLIENT_CODES.microphone, 'refused');
    await screen.findByRole('button', { name: /Hold to talk/ });
    await act(async () => {
      fireEvent.pointerDown(hold());
      await flush();
    });
    expect(await screen.findByText('The microphone could not be used.')).toBeTruthy();
    expect(hold()).toBeTruthy();
  });

  it('shows no button on a device without a recorder', async () => {
    await mountChat();
    await screen.findByText(/No messages yet/);
    expect(screen.queryByRole('button', { name: /Hold to talk/ })).toBeNull();
    expect(screen.getByRole('button', { name: 'Send' })).toBeTruthy();
  });
});

describe('what was heard', () => {
  it('shows the transcript with Confirm and Decline for a spoken goal, and confirms it', async () => {
    const chat = await mountChat({ recorder: true });
    chat.fake.respond('POST /v1/chat/audio', 202, voiceAccepted());
    chat.fake.respond(
      'POST /v1/goals/goalreq_1/confirm',
      200,
      goalView({
        state: 'PLANNING',
        needs_confirmation: false,
        confirmed_at: '2026-01-01T00:00:12Z',
      }),
    );
    await press(chat);
    await release();

    expect(await screen.findByText('write three haiku about bees')).toBeTruthy();
    expect(screen.getByText(/Nothing runs until you confirm it/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
    expect(await screen.findByText('Confirmed. The Queen is planning it.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Decline' })).toBeNull();
    expect(chat.paths()).toContain('POST /v1/goals/goalreq_1/confirm');
  });

  it('declines a spoken goal with a reason', async () => {
    const chat = await mountChat({ recorder: true });
    chat.fake.respond('POST /v1/chat/audio', 202, voiceAccepted());
    chat.fake.respond(
      'POST /v1/goals/goalreq_1/decline',
      200,
      goalView({ state: 'REFUSED', needs_confirmation: false, refused: true }),
    );
    await press(chat);
    await release();
    fireEvent.click(await screen.findByRole('button', { name: 'Decline' }));
    expect(await screen.findByText('Not run.')).toBeTruthy();
    const post = chat.fake.requests.find((r) => r.target.endsWith('/decline'))!;
    expect(JSON.parse(new TextDecoder().decode(post.body!))).toEqual({
      reason: 'declined by the human',
    });
  });

  it('sends a spoken answer to the chosen question and drops the question', async () => {
    const question = questionView({ id: 'msg_01M3AN9216QQX5JVNQHHWBFJR9', text: 'Which season?' });
    const chat = await mountChat({ recorder: true, inbox: inboxView({ questions: [question] }) });
    chat.fake.respond(
      'POST /v1/chat/audio',
      202,
      voiceAccepted({
        intent: 'answer',
        goal: null,
        transcript: 'spring',
        answered: {
          question_id: question.id,
          question_status: 'ANSWERED',
          task_id: 'task_1',
          task_status: 'RUNNING',
        },
      }),
    );
    fireEvent.change(await screen.findByLabelText('Speak as'), {
      target: { value: `answer:${question.id}` },
    });
    await press(chat);
    await release();

    expect(
      await screen.findByText('Answered. The task it was waiting on runs again.'),
    ).toBeTruthy();
    expect(audioPosts(chat)[0]!.target).toBe(
      `/v1/chat/audio?intent=answer%3A${question.id}&duration_s=2.5`,
    );
    await waitFor(() =>
      expect(screen.queryByRole('region', { name: 'Waiting for you' })).toBeNull(),
    );
  });

  it('shows a spoken message as sent until its line arrives', async () => {
    const chat = await mountChat({ recorder: true });
    chat.fake.respond(
      'POST /v1/chat/audio',
      202,
      voiceAccepted({
        intent: 'chat',
        goal: null,
        chat: { id: 'chat_5' },
        transcript: 'how did it go',
      }),
    );
    fireEvent.change(await screen.findByLabelText('Speak as'), { target: { value: 'chat' } });
    await press(chat);
    await release();
    expect(await screen.findByText('Sent, waiting for the Queen to log it')).toBeTruthy();
    expect(audioPosts(chat)[0]!.target).toBe('/v1/chat/audio?intent=chat&duration_s=2.5');
    await deliver(
      chat.socket,
      chatFrame({ id: 'chat_5', seq: 5, author: 'human', kind: 'message', text: 'how did it go' }),
    );
    expect(screen.queryByText('Sent, waiting for the Queen to log it')).toBeNull();
  });
});

// The public key the fake browser's stored session was made with, read from the store it saved to.
async function publicKeyOf(chat: MountedChat): Promise<string> {
  const stored = (await chat.fake.store.get('session')) as { publicKey: string } | undefined;
  return stored!.publicKey;
}
