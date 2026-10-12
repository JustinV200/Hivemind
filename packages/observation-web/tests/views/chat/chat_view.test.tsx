/**
 * Tests for the Chat view: the seed, the live stream, the inbox forms and the refusal.
 *
 * The screen is mounted on the fake browser and its one socket is driven by hand. The rules under
 * test: the seed is one chat read and one inbox read; a frame shows at once; the inbox is read
 * again once per live question or Alarm and never otherwise; an answer or an acknowledgement posts
 * to the right path and removes the item; a refusal of the chat read hides the composer.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/chat/ through `ChatScreen`.
 *
 * Key invariants:
 *   - No request is made that the test did not route; an unrouted request answers 404.
 *   - The view never polls: time passing alone adds no request.
 *
 * See Also:
 *   - tests/views/chat/slice.test.ts for the reducer rules.
 */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { flush } from '../../support/fake_browser';
import { deliver, mountChat } from './mount';
import { alarmView, chatFrame, chatLine, chatPage, inboxView, questionView } from './support';

const QUESTION = questionView({
  id: 'msg_Q1',
  options: ['winter', 'spring'],
  text: 'Which season?',
});
const QUESTION_LINE = chatLine({
  id: 'chat_2',
  seq: 2,
  kind: 'question',
  ref: 'msg_Q1',
  text: 'Which season?',
});
const ALARM_LINE = chatLine({
  id: 'chat_3',
  seq: 3,
  kind: 'alarm',
  ref: 'alarm_1',
  text: 'A Cell went quiet',
});

const reads = (paths: string[]) => paths.filter((p) => p.startsWith('GET'));

describe('seed', () => {
  it('reads the newest page and the inbox once each, and draws each line with its kind and time', async () => {
    const chat = await mountChat({
      page: chatPage([
        chatLine({
          id: 'chat_1',
          seq: 1,
          author: 'human',
          kind: 'message',
          text: 'tidy the garden',
        }),
        chatLine({ id: 'chat_2', seq: 2, kind: 'reply', text: 'On it', task_id: 'task_77' }),
      ]),
    });
    expect(await screen.findByText('tidy the garden')).toBeTruthy();
    // The two seed reads go out together, so their order is not fixed.
    expect(chat.fake.requests.map((r) => r.target).sort()).toEqual([
      '/v1/chat?limit=100',
      '/v1/inbox',
    ]);
    const [mine, hers] = screen.getAllByRole('article');
    expect(within(mine!).getByText('Message')).toBeTruthy();
    expect(within(hers!).getByText('Reply')).toBeTruthy();
    expect(mine!.querySelector('time')).not.toBeNull();
  });

  it('links a line to its task and a human message to the Queen thoughts', async () => {
    await mountChat({
      page: chatPage([
        chatLine({ id: 'chat_1', seq: 1, author: 'human', kind: 'message', text: 'go' }),
        chatLine({ id: 'chat_2', seq: 2, kind: 'reply', text: 'ok', task_id: 'task_77' }),
      ]),
    });
    await screen.findByText('go');
    const hrefs = screen.getAllByRole('link').map((a) => a.getAttribute('href'));
    expect(hrefs).toContain('#/thoughts/queen');
    expect(hrefs).toContain('#/attendant/queen');
  });

  it('says what would fill an empty conversation', async () => {
    await mountChat();
    expect(await screen.findByText(/No messages yet/)).toBeTruthy();
  });

  it('hides the composer and shows the sentence when the device may not submit', async () => {
    await mountChat({
      chatStatus: 403,
      chatBody: {
        error: 'hivemind.entrance.capability_denied',
        detail: 'no',
        capability: 'entrance:submit',
      },
    });
    expect(await screen.findByText('This device is not allowed to see or do that.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Send' })).toBeNull();
    expect(screen.queryByLabelText('Message to the Queen')).toBeNull();
  });
});

describe('live', () => {
  it('opens only the chat stream and shows a frame at once', async () => {
    const chat = await mountChat({ page: chatPage([chatLine()]) });
    expect(chat.fake.sockets.map((s) => s.url)).toEqual(['wss://hive.test/v1/chat/stream']);
    await deliver(chat.socket, chatFrame({ id: 'chat_2', seq: 2, text: 'A fresh reply' }));
    expect(screen.getByText('A fresh reply')).toBeTruthy();
  });

  it('resumes from the last line after the Entrance says it fell behind', async () => {
    const chat = await mountChat({ page: chatPage([chatLine()]) });
    await deliver(chat.socket, chatFrame({ id: 'chat_8', seq: 8, text: 'eight' }));
    await act(async () => {
      chat.socket.serverClose(4409);
      // The reconnect is a zero-delay timer on the fake clock.
      chat.fake.advance(0);
      await flush();
    });
    expect(chat.fake.sockets).toHaveLength(2);
    expect(chat.fake.sockets[1]!.url).toBe('wss://hive.test/v1/chat/stream?after=8');
  });

  it('reads the inbox once per live question or Alarm, and never without a frame', async () => {
    const chat = await mountChat();
    await screen.findByText(/No messages yet/);
    expect(reads(chat.paths()).sort()).toEqual(['GET /v1/chat', 'GET /v1/inbox']);

    // Time passing, and a plain reply, add no read.
    chat.fake.advance(10 * 60_000);
    await deliver(chat.socket, chatFrame({ id: 'chat_1', seq: 1, kind: 'reply' }));
    expect(reads(chat.paths())).toHaveLength(2);

    // A question and then an Alarm each add exactly one inbox read.
    await deliver(chat.socket, chatFrame({ ...QUESTION_LINE }));
    await waitFor(() => expect(reads(chat.paths())).toHaveLength(3));
    await deliver(chat.socket, chatFrame({ ...ALARM_LINE }));
    await waitFor(() => expect(reads(chat.paths())).toHaveLength(4));
    await act(() => flush());
    expect(reads(chat.paths()).slice(2)).toEqual(['GET /v1/inbox', 'GET /v1/inbox']);

    // The same question delivered again is a repeat and reads nothing.
    await deliver(chat.socket, chatFrame({ ...QUESTION_LINE }));
    await act(() => flush());
    expect(reads(chat.paths())).toHaveLength(4);
  });

  it('announces an arriving question once, politely', async () => {
    const chat = await mountChat({ inbox: inboxView({ questions: [QUESTION] }) });
    await screen.findByText(/No messages yet/);
    await deliver(chat.socket, chatFrame({ ...QUESTION_LINE }));
    const region = screen.getByRole('status', { name: '' });
    expect(region.getAttribute('aria-live')).toBe('polite');
    await waitFor(() => expect(region.textContent).toContain('Which season?'));
  });
});

describe('answering and acknowledging', () => {
  const setup = {
    page: chatPage([QUESTION_LINE, ALARM_LINE]),
    inbox: inboxView({ questions: [QUESTION], alarms: [alarmView()] }),
  };

  it('answers a closed question with the option and its index, then drops the form', async () => {
    const chat = await mountChat(setup);
    chat.fake.respond('POST /v1/inbox/questions/msg_Q1/answer', 200, {
      question_id: 'msg_Q1',
      question_status: 'ANSWERED',
      task_id: 'task_1',
      task_status: 'RUNNING',
    });
    fireEvent.click(await screen.findByRole('button', { name: 'spring' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'spring' })).toBeNull());
    const post = chat.fake.requests.find((r) => r.method === 'POST')!;
    expect(post.target).toBe('/v1/inbox/questions/msg_Q1/answer');
    expect(JSON.parse(new TextDecoder().decode(post.body!))).toEqual({
      text: 'spring',
      chosen_option: 1,
    });
    expect(screen.getAllByText('No longer waiting.')).toHaveLength(1);
  });

  it('answers a free-text question with the typed words', async () => {
    const free = questionView({ id: 'msg_Q1', options: [] });
    const chat = await mountChat({ ...setup, inbox: inboxView({ questions: [free] }) });
    chat.fake.respond('POST /v1/inbox/questions/msg_Q1/answer', 200, {
      question_id: 'msg_Q1',
      question_status: 'ANSWERED',
      task_id: 'task_1',
      task_status: 'RUNNING',
    });
    fireEvent.change(await screen.findByPlaceholderText('Your answer'), {
      target: { value: ' autumn ' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Answer' }));
    await waitFor(() => expect(screen.queryByPlaceholderText('Your answer')).toBeNull());
    const post = chat.fake.requests.find((r) => r.method === 'POST')!;
    expect(JSON.parse(new TextDecoder().decode(post.body!))).toEqual({ text: 'autumn' });
  });

  it('acknowledges an Alarm and drops its button', async () => {
    const chat = await mountChat(setup);
    chat.fake.respond('POST /v1/inbox/alarms/alarm_1/acknowledge', 200, { acknowledged: true });
    expect(await screen.findByText('CRITICAL')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Acknowledge' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Acknowledge' })).toBeNull());
    expect(chat.paths()).toContain('POST /v1/inbox/alarms/alarm_1/acknowledge');
  });

  it('removes a question someone else answered first and says so', async () => {
    const chat = await mountChat(setup);
    chat.fake.respond('POST /v1/inbox/questions/msg_Q1/answer', 409, {
      error: 'hivemind.conflict',
      detail: 'already answered',
    });
    fireEvent.click(await screen.findByRole('button', { name: 'winter' }));
    expect(await screen.findByText(/already dealt with on another device/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'winter' })).toBeNull();
  });

  it('keeps the form and shows the refusal when the answer is refused', async () => {
    const chat = await mountChat(setup);
    chat.fake.respond('POST /v1/inbox/questions/msg_Q1/answer', 422, {
      error: 'hivemind.entrance.invalid_request',
      detail: 'bad',
    });
    fireEvent.click(await screen.findByRole('button', { name: 'winter' }));
    expect(await screen.findByText('The Hive could not read that request.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'winter' })).toBeTruthy();
  });

  it('shows a waiting question whose line is not in the conversation', async () => {
    await mountChat({ inbox: inboxView({ questions: [QUESTION] }) });
    const section = await screen.findByRole('region', { name: 'Waiting for you' });
    expect(within(section).getByRole('button', { name: 'spring' })).toBeTruthy();
  });
});
