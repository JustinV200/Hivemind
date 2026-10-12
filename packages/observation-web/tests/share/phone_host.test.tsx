/**
 * Tests for the phone host: the share sheet's confirmation and send, held items, the push offer and
 * the invite box.
 *
 * ADR-0047: a shared item is shown with where it will go before anything is posted; text goes to
 * `POST /v1/chat`, a clip to `POST /v1/chat/audio` with `intent=chat`, an image to
 * `POST /v1/nectar/images`; an item shared while logged out is held in memory until login. A plain
 * browser, which has none of the phone's members, gets no markup at all.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/share/ through `PhoneHost`.
 *
 * Key invariants:
 *   - No request is made before the Send tap, and Cancel makes none at all.
 *   - Requests are asserted from what the fake Entrance recorded: path, content type and body.
 *
 * See Also:
 *   - tests/share/support.tsx for the harness, tests/views/chat for the chat's own writes.
 */
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { flush } from '../support/fake_browser';
import type { FakeBrowser } from '../support/fake_browser';
import { voiceAccepted } from '../views/chat/support';
import { FakeIntake, openPhone } from './support';

const writes = (fake: FakeBrowser) => fake.requests.filter((r) => r.method !== 'GET');
const BYTES = Uint8Array.from([1, 2, 3, 4]);

afterEach(() => {
  window.location.hash = '';
});

describe('share intake', () => {
  it('shows what will be sent and where, and posts nothing before Send', async () => {
    const intake = new FakeIntake();
    const phone = await openPhone({ intake });
    act(() => intake.share({ kind: 'text', text: 'buy more frames' }));
    expect(await screen.findByText('Send to the Hive?')).toBeTruthy();
    expect(screen.getByText('buy more frames')).toBeTruthy();
    expect(screen.getByText('https://hive.example.ts.net')).toBeTruthy();
    expect(writes(phone.fake)).toHaveLength(0);
  });

  it('sends shared text to the chat and goes to the chat view', async () => {
    const intake = new FakeIntake();
    const phone = await openPhone({ intake });
    phone.fake.respond('POST /v1/chat', 202, { id: 'chat_1' });
    act(() => intake.share({ kind: 'text', text: 'buy more frames' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Send' }));
    await waitFor(() => expect(writes(phone.fake)).toHaveLength(1));
    const post = writes(phone.fake)[0]!;
    expect(post.target).toBe('/v1/chat');
    expect(JSON.parse(new TextDecoder().decode(post.body!))).toEqual({ text: 'buy more frames' });
    await waitFor(() => expect(screen.queryByText('Send to the Hive?')).toBeNull());
    expect(window.location.hash).toBe('#/chat');
  });

  it('sends a shared clip to the audio route as a chat message, in its own format', async () => {
    const intake = new FakeIntake();
    const phone = await openPhone({
      intake,
      measure: () => Promise.resolve(3.25),
    });
    phone.fake.respond('POST /v1/chat/audio', 200, voiceAccepted({ intent: 'chat' }));
    act(() => intake.share({ kind: 'audio', bytes: BYTES, mediaType: 'audio/x-m4a' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Send' }));
    await waitFor(() => expect(writes(phone.fake)).toHaveLength(1));
    const post = writes(phone.fake)[0]!;
    expect(post.target).toBe('/v1/chat/audio?intent=chat&duration_s=3.25');
    expect(post.headers['Content-Type']).toBe('audio/mp4');
    expect([...post.body!]).toEqual([1, 2, 3, 4]);
  });

  it('sends a shared image to the image intake route under its media type', async () => {
    const intake = new FakeIntake();
    const phone = await openPhone({ intake });
    phone.fake.respond('POST /v1/nectar/images', 201, { id: 'nectar_1' });
    act(() => intake.share({ kind: 'image', bytes: BYTES, mediaType: 'image/png' }));
    expect(await screen.findByText(/An image \(image\/png/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(writes(phone.fake)).toHaveLength(1));
    const post = writes(phone.fake)[0]!;
    expect(post.target).toBe('/v1/nectar/images');
    expect(post.headers['Content-Type']).toBe('image/png');
    expect([...post.body!]).toEqual([1, 2, 3, 4]);
    expect(post.headers.Authorization).toBeDefined();
  });

  it('sends nothing when the human cancels', async () => {
    const intake = new FakeIntake();
    const phone = await openPhone({ intake });
    act(() => intake.share({ kind: 'text', text: 'never mind' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByText('Send to the Hive?')).toBeNull());
    expect(writes(phone.fake)).toHaveLength(0);
  });

  it('shows a refusal in place and keeps the card so the human can retry', async () => {
    const intake = new FakeIntake();
    const phone = await openPhone({ intake });
    phone.fake.respond('POST /v1/nectar/images', 413, {
      error: 'hivemind.entrance.body_too_large',
      detail: 'That image is too large.',
    });
    act(() => intake.share({ kind: 'image', bytes: BYTES, mediaType: 'image/png' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Send' }));
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Send' })).toBeTruthy();
  });

  it('asks for the password when the Entrance wants a step-up', async () => {
    const intake = new FakeIntake();
    const phone = await openPhone({ intake });
    phone.fake.respond('POST /v1/nectar/images', 403, {
      error: 'hivemind.entrance.step_up_required',
      detail: 'confirm again',
      reason: 'sensitive_action',
    });
    act(() => intake.share({ kind: 'image', bytes: BYTES, mediaType: 'image/png' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Send' }));
    expect(await screen.findByRole('form', { name: 'Confirm again' })).toBeTruthy();
  });

  it('offers only Dismiss for an item the phone could not take', async () => {
    const intake = new FakeIntake();
    await openPhone({ intake });
    act(() => intake.share({ kind: 'refused', reason: 'too_large' }));
    expect(await screen.findByRole('button', { name: 'Dismiss' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Send' })).toBeNull();
  });

  it('shows an item that arrived before the host was listening', async () => {
    const intake = new FakeIntake();
    intake.share({ kind: 'text', text: 'shared at launch' });
    await openPhone({ intake });
    expect(await screen.findByText('shared at launch')).toBeTruthy();
  });

  it('asks about queued items one at a time', async () => {
    const intake = new FakeIntake();
    await openPhone({ intake });
    act(() => {
      intake.share({ kind: 'text', text: 'first' });
      intake.share({ kind: 'text', text: 'second' });
    });
    expect(await screen.findByText('first')).toBeTruthy();
    expect(screen.queryByText('second')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(await screen.findByText('second')).toBeTruthy();
  });

  it('holds an item shared while logged out and shows it after login, sending nothing meanwhile', async () => {
    const intake = new FakeIntake();
    const phone = await openPhone({ intake, signedIn: false });
    act(() => intake.share({ kind: 'text', text: 'held for later' }));
    expect(await screen.findByText(/Log in to review it/)).toBeTruthy();
    expect(screen.queryByText('held for later')).toBeNull();
    expect(phone.fake.requests).toHaveLength(0);
    await phone.logIn();
    expect(await screen.findByText('held for later')).toBeTruthy();
    expect(writes(phone.fake)).toHaveLength(0);
  });

  it('stops listening to the share sheet when it unmounts', async () => {
    const intake = new FakeIntake();
    const phone = await openPhone({ intake });
    phone.view.unmount();
    expect(intake.stopped).toBe(1);
  });
});

describe('a plain browser', () => {
  it('renders nothing at all', async () => {
    const phone = await openPhone();
    expect(phone.view.container.innerHTML).toBe('');
  });
});

describe('invite box', () => {
  it('asks for the invite link until an address is stored, and tells a wrong paste', async () => {
    const accepted: string[] = [];
    const invites = {
      configured: false,
      accept: (link: string) => {
        accepted.push(link);
        return Promise.resolve(false);
      },
    };
    await openPhone({ invites, signedIn: false });
    fireEvent.change(screen.getByLabelText('Invite link'), { target: { value: 'nonsense' } });
    fireEvent.click(screen.getByRole('button', { name: 'Use this link' }));
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(accepted).toEqual(['nonsense']);
  });

  it('is not drawn once the address is known', async () => {
    await openPhone({ invites: { configured: true, accept: () => Promise.resolve(true) } });
    expect(screen.queryByLabelText('Invite link')).toBeNull();
  });
});

describe('push offer', () => {
  it('registers through the client when tapped, then goes away', async () => {
    const phone = await openPhone({ distributor: true });
    phone.fake.respond('GET /v1/push/vapid-key', 200, { application_server_key: 'AQID' });
    phone.fake.respond('POST /v1/push/subscriptions', 201, {
      id: 'sub_1',
      channel: 'web_push',
      created_at: 'x',
    });
    expect(writes(phone.fake)).toHaveLength(0);
    fireEvent.click(await screen.findByRole('button', { name: 'Turn on push notices' }));
    await waitFor(() => expect(writes(phone.fake)).toHaveLength(1));
    expect(writes(phone.fake)[0]!.target).toBe('/v1/push/subscriptions');
    await waitFor(() => expect(screen.queryByText(/Get a notice/)).toBeNull());
  });

  it('shows the reason when registration fails and stays', async () => {
    const phone = await openPhone({ distributor: true });
    phone.browser.subscribeWebPush = () =>
      Promise.reject(new Error('No UnifiedPush distributor is installed.'));
    phone.fake.respond('GET /v1/push/vapid-key', 200, { application_server_key: 'AQID' });
    fireEvent.click(await screen.findByRole('button', { name: 'Turn on push notices' }));
    expect(await screen.findByText('No UnifiedPush distributor is installed.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Turn on push notices' })).toBeTruthy();
  });

  it('remembers "Not now"', async () => {
    const phone = await openPhone({ distributor: true });
    fireEvent.click(await screen.findByRole('button', { name: 'Not now' }));
    phone.view.unmount();
    await flush();
    const again = await openPhone({ distributor: true });
    expect(again.fake.store).not.toBe(phone.fake.store);
    await phone.fake.store.get('push_declined').then((value) => expect(value).toBe(true));
  });

  it('is not offered without a session, or in a browser', async () => {
    await openPhone({ distributor: true, signedIn: false });
    expect(screen.queryByText(/Get a notice/)).toBeNull();
  });
});
