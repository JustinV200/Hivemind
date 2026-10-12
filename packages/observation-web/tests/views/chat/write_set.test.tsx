/**
 * The Chat view is the only writer: every request it makes is in one fixed set of routes.
 *
 * Codingrules 8.11 gives the UI one write path, the message into the Queen's inbox. This test runs
 * every interaction the view has (seed, a live question and Alarm, an answer, an acknowledgement,
 * a typed message, a spoken goal confirmed, a spoken goal declined) on one mounted screen and
 * collects every request the fake browser saw. The set of `METHOD path` pairs must be exactly the
 * reads and writes of step 12.8, and the only socket must be the chat stream.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/chat/ end to end on the fake browser.
 *
 * Key invariants:
 *   - Paths are compared without their query; ids are the ones the test gave.
 *   - Adding a request to the view without adding it here fails this test.
 *
 * See Also:
 *   - tests/shell/write_routes.test.ts for the client's allow-list.
 */
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { expect, it } from 'vitest';
import { flush } from '../../support/fake_browser';
import { deliver, mountChat } from './mount';
import {
  alarmView,
  chatFrame,
  chatLine,
  chatPage,
  goalView,
  inboxView,
  questionView,
  voiceAccepted,
} from './support';

const EXPECTED = [
  'GET /v1/chat',
  'GET /v1/inbox',
  'POST /v1/chat',
  'POST /v1/chat/audio',
  'POST /v1/inbox/alarms/alarm_1/acknowledge',
  'POST /v1/inbox/questions/msg_Q1/answer',
  'POST /v1/goals/goalreq_1/confirm',
  'POST /v1/goals/goalreq_2/decline',
].sort();

it('makes only the reads and the writes of the chat, across every interaction', async () => {
  const question = questionView({ id: 'msg_Q1', options: ['yes', 'no'] });
  const chat = await mountChat({
    recorder: true,
    page: chatPage([chatLine({ id: 'chat_1', seq: 1, kind: 'question', ref: 'msg_Q1' })]),
    inbox: inboxView({ questions: [question] }),
  });
  const { fake } = chat;
  fake.respond('POST /v1/inbox/questions/msg_Q1/answer', 200, {
    question_id: 'msg_Q1',
    question_status: 'ANSWERED',
    task_id: 'task_1',
    task_status: 'RUNNING',
  });
  fake.respond('POST /v1/inbox/alarms/alarm_1/acknowledge', 200, { acknowledged: true });
  fake.respond('POST /v1/chat', 202, { id: 'chat_9' });
  fake.respond(
    'POST /v1/goals/goalreq_1/confirm',
    200,
    goalView({ needs_confirmation: false, state: 'PLANNING' }),
  );
  fake.respond(
    'POST /v1/goals/goalreq_2/decline',
    200,
    goalView({ id: 'goalreq_2', needs_confirmation: false, state: 'REFUSED', refused: true }),
  );
  let goals = 0;
  fake.routes.set('POST /v1/chat/audio', () => ({
    status: 202,
    body: voiceAccepted({ goal: goalView({ id: `goalreq_${(goals += 1)}` }) }),
  }));

  // A live Alarm: one more inbox read, and its Acknowledge button.
  fake.respond('GET /v1/inbox', 200, inboxView({ questions: [question], alarms: [alarmView()] }));
  await deliver(chat.socket, chatFrame({ id: 'chat_2', seq: 2, kind: 'alarm', ref: 'alarm_1' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Acknowledge' }));
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Acknowledge' })).toBeNull());

  // An answer and a typed message.
  fireEvent.click(await screen.findByRole('button', { name: 'yes' }));
  await waitFor(() => expect(screen.queryByRole('button', { name: 'yes' })).toBeNull());
  fireEvent.change(screen.getByLabelText('Message to the Queen'), { target: { value: 'hello' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send' }));
  await screen.findByText('Sent, waiting for the Queen to log it');

  // Two spoken goals: one confirmed, one declined.
  for (const verdict of ['Confirm', 'Decline']) {
    await act(async () => {
      fireEvent.pointerDown(screen.getByRole('button', { name: /Hold to talk/ }));
      await flush();
    });
    await act(async () => {
      fireEvent.pointerUp(screen.getByRole('button', { name: /Recording/ }));
      await flush();
    });
    fireEvent.click(await screen.findByRole('button', { name: verdict }));
    await waitFor(() => expect(screen.queryByRole('button', { name: verdict })).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
  }

  const seen = [...new Set(chat.paths())].sort();
  expect(seen).toEqual(EXPECTED);
  expect(fake.sockets.map((s) => s.url)).toEqual(['wss://hive.test/v1/chat/stream']);
});
