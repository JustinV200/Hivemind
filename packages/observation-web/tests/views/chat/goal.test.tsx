/**
 * Tests for "Request as a goal": the switch, the post, the line, the controls and the step-up.
 *
 * A chat message never starts a task, so the composer has a switch (off by default) that turns the
 * same box into a goal request. The words go to `POST /v1/goals` with the documented defaults, the
 * answer shows as a line in the conversation that links to the Queen's Attendant view, a goal held
 * for the human's yes gets Confirm and Decline, and a `403 step_up_required` asks for the password
 * and retries once. A refusal keeps the words in the box.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/chat/composer/, writes/use_goal.ts and
 *   conversation/goal_row.tsx on the fake browser.
 *
 * Key invariants:
 *   - Requests are counted from what the fake recorded, not from the view's own state.
 *
 * See Also:
 *   - tests/views/chat/composer.test.tsx for the same step-up flow on a plain message.
 *   - tests/views/chat/write_set.test.tsx for the whole set of routes the chat may call.
 */
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { FakeBrowser, RecordedRequest } from '../../support/fake_browser';
import { mountChat } from './mount';
import { goalView } from './support';

const STEP_UP = {
  error: 'hivemind.entrance.step_up_required',
  detail: 'confirm again',
  reason: 'sensitive_action',
};

const posts = (fake: FakeBrowser, path: string): RecordedRequest[] =>
  fake.requests.filter((r) => r.method === 'POST' && r.target === path);

const bodyOf = (request: RecordedRequest): unknown =>
  JSON.parse(new TextDecoder().decode(request.body!));

const box = (): HTMLTextAreaElement =>
  screen.getByLabelText('Message to the Queen') as HTMLTextAreaElement;

function type(text: string): void {
  fireEvent.change(box(), { target: { value: text } });
}

const switchOn = (): void => {
  fireEvent.click(screen.getByRole('switch', { name: 'Request as a goal' }));
};

describe('the switch', () => {
  it('is off by default, and a plain Send still posts a chat message', async () => {
    const chat = await mountChat();
    chat.fake.respond('POST /v1/chat', 202, { id: 'chat_9' });
    await screen.findByText(/No messages yet/);
    const toggle = screen.getByRole('switch', { name: 'Request as a goal' }) as HTMLInputElement;
    expect(toggle.checked).toBe(false);
    type('hello');
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(posts(chat.fake, '/v1/chat')).toHaveLength(1));
    expect(posts(chat.fake, '/v1/goals')).toHaveLength(0);
  });

  it('turns the button into "Request goal" and back', async () => {
    await mountChat();
    await screen.findByText(/No messages yet/);
    switchOn();
    expect(screen.getByRole('button', { name: 'Request goal' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Send' })).toBeNull();
    switchOn();
    expect(screen.getByRole('button', { name: 'Send' })).toBeTruthy();
  });
});

describe('requesting', () => {
  it('posts the words with the default ceiling, then shows a line that links to the Queen', async () => {
    const chat = await mountChat();
    chat.fake.respond('POST /v1/goals', 202, { id: 'goalreq_7', state: 'RECEIVED' });
    await screen.findByText(/No messages yet/);
    switchOn();
    type('  write three haiku about bees  ');
    fireEvent.click(screen.getByRole('button', { name: 'Request goal' }));

    await screen.findByLabelText('You, requested a goal');
    expect(bodyOf(posts(chat.fake, '/v1/goals')[0]!)).toEqual({
      text: 'write three haiku about bees',
      clearance: 'C1',
    });
    expect(box().value).toBe('');
    expect(posts(chat.fake, '/v1/chat')).toHaveLength(0);
    expect(screen.getByText('write three haiku about bees')).toBeTruthy();
    expect(screen.getByText(/She plans it on her next tick/)).toBeTruthy();
    // A goal that is not held has nothing to confirm.
    expect(screen.queryByRole('button', { name: 'Confirm' })).toBeNull();
    const link = screen.getByRole('link', { name: /goalreq_7/ }) as HTMLAnchorElement;
    expect(link.getAttribute('href')).toBe('#/attendant/queen');
  });

  it('gives a goal held for the human Confirm and Decline, and confirms it', async () => {
    const chat = await mountChat();
    chat.fake.respond('POST /v1/goals', 202, { id: 'goalreq_8', state: 'AWAITING_CONFIRMATION' });
    chat.fake.respond(
      'POST /v1/goals/goalreq_8/confirm',
      200,
      goalView({ id: 'goalreq_8', needs_confirmation: false, state: 'PLANNING', source: 'typed' }),
    );
    await screen.findByText(/No messages yet/);
    switchOn();
    type('tidy the garden');
    fireEvent.click(screen.getByRole('button', { name: 'Request goal' }));

    expect(await screen.findByRole('button', { name: 'Decline' })).toBeTruthy();
    expect(screen.getByText(/Nothing runs until you confirm it/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
    expect(await screen.findByText('Confirmed. The Queen is planning it.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Confirm' })).toBeNull();
    expect(posts(chat.fake, '/v1/goals/goalreq_8/confirm')).toHaveLength(1);
  });

  it('keeps the words and shows the refusal when the Entrance says no', async () => {
    const chat = await mountChat();
    chat.fake.respond('POST /v1/goals', 403, {
      error: 'hivemind.entrance.capability_denied',
      detail: 'x',
    });
    await screen.findByText(/No messages yet/);
    switchOn();
    type('do something');
    fireEvent.click(screen.getByRole('button', { name: 'Request goal' }));

    expect(await screen.findByText('This device is not allowed to see or do that.')).toBeTruthy();
    expect(box().value).toBe('do something');
    expect(screen.queryByLabelText('You, requested a goal')).toBeNull();
  });
});

describe('step-up', () => {
  function needStepUp(chat: Awaited<ReturnType<typeof mountChat>>, refusals = 1): void {
    let refused = 0;
    chat.fake.routes.set('POST /v1/goals', () =>
      refused < refusals
        ? (refused++, { status: 403, body: STEP_UP })
        : { status: 202, body: { id: 'goalreq_9', state: 'RECEIVED' } },
    );
    chat.fake.respond('POST /v1/auth/step-up/challenge', 200, { nonce: 'n1', passkey_options: {} });
    chat.fake.respond('POST /v1/auth/step-up', 200, { stepped_up_until: '2030-01-01T00:00:00Z' });
  }

  it('asks for the password and retries the request once', async () => {
    const chat = await mountChat();
    needStepUp(chat);
    await screen.findByText(/No messages yet/);
    switchOn();
    type('spend a lot');
    fireEvent.click(screen.getByRole('button', { name: 'Request goal' }));

    const prompt = await screen.findByRole('form', { name: 'Confirm again' });
    expect(prompt.textContent).toContain('This needs you to confirm again.');
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'hunter2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));

    await screen.findByLabelText('You, requested a goal');
    expect(posts(chat.fake, '/v1/goals')).toHaveLength(2);
    expect(posts(chat.fake, '/v1/goals').map(bodyOf)).toEqual([
      { text: 'spend a lot', clearance: 'C1' },
      { text: 'spend a lot', clearance: 'C1' },
    ]);
    expect(posts(chat.fake, '/v1/auth/step-up')).toHaveLength(1);
  });

  it('does not ask twice when the retry is refused again, and keeps the words', async () => {
    const chat = await mountChat();
    needStepUp(chat, 2);
    await screen.findByText(/No messages yet/);
    switchOn();
    type('spend a lot');
    fireEvent.click(screen.getByRole('button', { name: 'Request goal' }));
    fireEvent.change(await screen.findByLabelText('Password'), { target: { value: 'pw' } });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));

    await waitFor(() => expect(posts(chat.fake, '/v1/goals')).toHaveLength(2));
    await waitFor(() => expect(screen.queryByRole('form', { name: 'Confirm again' })).toBeNull());
    expect(posts(chat.fake, '/v1/auth/step-up')).toHaveLength(1);
    expect(box().value).toBe('spend a lot');
  });
});
