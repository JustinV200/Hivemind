/**
 * Tests for the composer: sending, the pending message, a refusal, and the step-up prompt.
 *
 * A typed message goes to `POST /v1/chat`, shows at once as pending and settles when its line
 * arrives on the stream under the id the answer returned. A `403 step_up_required` shows the
 * password prompt, steps up through the session client, and retries the message exactly once;
 * a second refusal is shown and does not ask again, and a cancel keeps the words as not sent.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/chat/composer/ and src/views/chat/writes/.
 *
 * Key invariants:
 *   - The step-up routes are answered by the fake; the password is typed into the real prompt.
 *   - A retry is counted from the requests the fake recorded, not from the view's own state.
 *
 * See Also:
 *   - tests/views/chat/chat_view.test.tsx for the reads and the inbox forms.
 */
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { FakeBrowser, RecordedRequest } from '../../support/fake_browser';
import { deliver, mountChat } from './mount';
import { chatFrame } from './support';

const STEP_UP = {
  error: 'hivemind.entrance.step_up_required',
  detail: 'confirm again',
  reason: 'sensitive_action',
};

const posts = (fake: FakeBrowser, path: string): RecordedRequest[] =>
  fake.requests.filter((r) => r.method === 'POST' && r.target === path);

const bodyOf = (request: RecordedRequest): unknown =>
  JSON.parse(new TextDecoder().decode(request.body!));

function type(text: string): void {
  fireEvent.change(screen.getByLabelText('Message to the Queen'), { target: { value: text } });
}

describe('sending', () => {
  it('posts the trimmed words, shows them as pending, and settles on the line with the same id', async () => {
    const chat = await mountChat();
    chat.fake.respond('POST /v1/chat', 202, { id: 'chat_9' });
    await screen.findByText(/No messages yet/);
    type('  tidy the garden  ');
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    expect(await screen.findByText('tidy the garden')).toBeTruthy();
    await waitFor(() => expect(posts(chat.fake, '/v1/chat')).toHaveLength(1));
    expect(bodyOf(posts(chat.fake, '/v1/chat')[0]!)).toEqual({ text: 'tidy the garden' });
    // The box cleared at once, and the message waits for its own line.
    expect((screen.getByLabelText('Message to the Queen') as HTMLTextAreaElement).value).toBe('');
    await screen.findByText('Sent, waiting for the Queen to log it');

    await deliver(
      chat.socket,
      chatFrame({
        id: 'chat_9',
        seq: 9,
        author: 'human',
        kind: 'message',
        text: 'tidy the garden',
      }),
    );
    expect(screen.queryByText('Sent, waiting for the Queen to log it')).toBeNull();
    expect(screen.getAllByText('tidy the garden')).toHaveLength(1);
  });

  it('will not send empty words, and sends on Ctrl+Enter', async () => {
    const chat = await mountChat();
    chat.fake.respond('POST /v1/chat', 202, { id: 'chat_9' });
    await screen.findByText(/No messages yet/);
    expect((screen.getByRole('button', { name: 'Send' }) as HTMLButtonElement).disabled).toBe(true);
    type('hello');
    fireEvent.keyDown(screen.getByLabelText('Message to the Queen'), {
      key: 'Enter',
      ctrlKey: true,
    });
    await waitFor(() => expect(posts(chat.fake, '/v1/chat')).toHaveLength(1));
  });

  it('keeps a refused message with the refusal until it is dismissed', async () => {
    const chat = await mountChat();
    chat.fake.respond('POST /v1/chat', 422, {
      error: 'hivemind.entrance.invalid_request',
      detail: 'bad',
    });
    await screen.findByText(/No messages yet/);
    type('hello');
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(await screen.findByText('The Hive could not read that request.')).toBeTruthy();
    expect(screen.getByText('Not sent')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByText('hello')).toBeNull();
  });
});

describe('step-up', () => {
  // POST /v1/chat is refused until a step-up has been done, then accepted.
  function needStepUp(chat: Awaited<ReturnType<typeof mountChat>>, refusals = 1): void {
    let refused = 0;
    chat.fake.routes.set('POST /v1/chat', () =>
      refused < refusals
        ? (refused++, { status: 403, body: STEP_UP })
        : { status: 202, body: { id: 'chat_9' } },
    );
    chat.fake.respond('POST /v1/auth/step-up/challenge', 200, { nonce: 'n1', passkey_options: {} });
    chat.fake.respond('POST /v1/auth/step-up', 200, { stepped_up_until: '2030-01-01T00:00:00Z' });
  }

  it('asks for the password, steps up, and retries the message once', async () => {
    const chat = await mountChat();
    needStepUp(chat);
    await screen.findByText(/No messages yet/);
    type('spend a lot');
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    const prompt = await screen.findByRole('form', { name: 'Confirm again' });
    expect(prompt.textContent).toContain('This needs you to confirm again.');
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'hunter2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));

    await waitFor(() => expect(posts(chat.fake, '/v1/chat')).toHaveLength(2));
    const stepBody = bodyOf(posts(chat.fake, '/v1/auth/step-up')[0]!) as { password: string };
    expect(stepBody.password).toBe('hunter2');
    // Both attempts carried the same words; the prompt is gone.
    expect(posts(chat.fake, '/v1/chat').map(bodyOf)).toEqual([
      { text: 'spend a lot' },
      { text: 'spend a lot' },
    ]);
    await waitFor(() => expect(screen.queryByRole('form', { name: 'Confirm again' })).toBeNull());
    await screen.findByText('Sent, waiting for the Queen to log it');
  });

  it('does not ask a second time when the retry is refused again', async () => {
    const chat = await mountChat();
    needStepUp(chat, 2);
    await screen.findByText(/No messages yet/);
    type('spend a lot');
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    fireEvent.change(await screen.findByLabelText('Password'), { target: { value: 'pw' } });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));

    expect(await screen.findByText('Not sent')).toBeTruthy();
    expect(posts(chat.fake, '/v1/chat')).toHaveLength(2);
    expect(posts(chat.fake, '/v1/auth/step-up')).toHaveLength(1);
    expect(screen.queryByRole('form', { name: 'Confirm again' })).toBeNull();
  });

  it('keeps asking after a wrong password, and gives up on cancel', async () => {
    const chat = await mountChat();
    needStepUp(chat);
    chat.fake.respond('POST /v1/auth/step-up', 401, {
      error: 'hivemind.entrance.authentication_failed',
      detail: 'no',
    });
    await screen.findByText(/No messages yet/);
    type('spend a lot');
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    fireEvent.change(await screen.findByLabelText('Password'), { target: { value: 'wrong' } });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));

    // The refusal is shown inside a fresh prompt, and the message was not retried.
    expect(await screen.findByText('The Hive did not accept this login.')).toBeTruthy();
    expect(posts(chat.fake, '/v1/chat')).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(await screen.findByText('Not sent')).toBeTruthy();
    expect(posts(chat.fake, '/v1/chat')).toHaveLength(1);
  });
});
