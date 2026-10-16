/**
 * Tests for the Chat's step-up: a refused read is asked for in place, retried, and read again later.
 *
 * A session the travel lock flagged is refused `403 step_up_required` on its reads too. The rules
 * under test: the composer and the prompt stay on such a refusal (unlike `capability_denied`,
 * which hides the composer); the password typed there lets the read through and clears the
 * session's `needsStepUp`, so the shell's banner goes; a cancelled prompt leaves the refusal and
 * the composer, and a step-up done for a later write reads the chat again without asking twice.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/chat/reads/use_chat.ts and
 *   src/views/chat/chat_screen.tsx with the shared src/views/devices/step_up/.
 *
 * Key invariants:
 *   - The step-up endpoints are the fake's; no password leaves the test.
 *
 * See Also:
 *   - tests/views/chat/chat_view.test.tsx for the `capability_denied` refusal.
 */
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { mountChat } from './mount';
import type { MountedChat } from './mount';
import { chatLine, chatPage } from './support';

const STEP_UP = { error: 'hivemind.entrance.step_up_required', detail: 'Step-up is required.' };
const ASKING = 'This needs you to confirm again.';

const chatReads = (chat: MountedChat) => chat.paths().filter((p) => p === 'GET /v1/chat');

// The Entrance accepts the next step-up, and the chat read answers with one line after it.
function acceptStepUp(chat: MountedChat): void {
  chat.fake.respond('POST /v1/auth/step-up/challenge', 200, { nonce: 'n', passkey_options: {} });
  chat.fake.respond('POST /v1/auth/step-up', 200, { stepped_up_until: '2030-01-01T00:00:00Z' });
  chat.fake.respond('GET /v1/chat', 200, chatPage([chatLine({ text: 'Hello from the Queen' })]));
}

async function typePassword(): Promise<void> {
  fireEvent.change(await screen.findByLabelText('Password'), { target: { value: 'pw' } });
  fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
}

describe('a chat read refused for a step-up', () => {
  it('keeps the composer, asks in place, reads again and clears the shell flag', async () => {
    const chat = await mountChat({ chatStatus: 403, chatBody: STEP_UP });
    // The shell believes the session is flagged, as a login under the travel lock says.
    chat.hive.store.dispatch({
      slice: 'session',
      event: {
        type: 'opened',
        session: { deviceId: 'd', listener: 'remote', expiresAt: 'x', needsStepUp: true },
      },
    });
    expect(await screen.findByRole('form', { name: 'Confirm again' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Send' })).toBeTruthy();

    acceptStepUp(chat);
    await typePassword();
    expect(await screen.findByText('Hello from the Queen')).toBeTruthy();
    expect(screen.queryByRole('form', { name: 'Confirm again' })).toBeNull();
    expect(screen.queryByText(ASKING)).toBeNull();
    expect(chat.hive.store.getSnapshot().session.needsStepUp).toBe(false);
    expect(chatReads(chat)).toHaveLength(2);
  });

  it('shows the refusal with the composer when cancelled, and reads again after a write steps up', async () => {
    const chat = await mountChat({ chatStatus: 403, chatBody: STEP_UP });
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }));
    expect(await screen.findByText(ASKING)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Send' })).toBeTruthy();
    // A cancel asks nothing more and reads nothing more.
    expect(chatReads(chat)).toHaveLength(1);

    // The write is refused the same way once; the password typed for it lets everything through.
    let posts = 0;
    chat.fake.routes.set('POST /v1/chat', () =>
      (posts += 1) === 1 ? { status: 403, body: STEP_UP } : { status: 202, body: { id: 'chat_9' } },
    );
    acceptStepUp(chat);
    fireEvent.change(screen.getByLabelText('Message to the Queen'), { target: { value: 'hi' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await typePassword();
    expect(await screen.findByText('Hello from the Queen')).toBeTruthy();
    await waitFor(() => expect(posts).toBe(2));
    expect(screen.queryByText(ASKING)).toBeNull();
    expect(chatReads(chat)).toHaveLength(2);
  });
});
