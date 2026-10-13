/**
 * Mount helpers for the Chat view tests: render the screen, open its socket, push a frame.
 *
 * The screen is rendered directly under a `HiveProvider` built by `openChat`, because the app's
 * outlet does not point at it until the orchestrator registers the slice. The view opens exactly
 * one socket (the chat stream), so the helper waits for it and opens it, as the shell's other tests
 * do for their streams.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Used by the view, voice and write-set tests of the Chat.
 *
 * Key invariants:
 *   - Nothing here asserts; a test that wants a request or a frame asserts on `OpenedChat` itself.
 *
 * See Also:
 *   - tests/views/chat/support.ts for `openChat` and the builders.
 */
import { act, render, waitFor } from '@testing-library/react';
import { expect } from 'vitest';
import { HiveProvider } from '../../../src/store';
import { ChatScreen } from '../../../src/views/chat';
import { flush } from '../../support/fake_browser';
import type { FakeSocket } from '../../support/fake_browser';
import { openChat } from './support';
import type { ChatSetup, OpenedChat } from './support';

export interface MountedChat extends OpenedChat {
  view: ReturnType<typeof render>;
  socket: FakeSocket;
}

export async function mountChat(setup: ChatSetup = {}): Promise<MountedChat> {
  const opened = await openChat(setup);
  const view = render(
    <HiveProvider hive={opened.hive}>
      <ChatScreen />
    </HiveProvider>,
  );
  await waitFor(() => expect(opened.fake.sockets).toHaveLength(1));
  const socket = opened.fake.sockets[0]!;
  await act(async () => {
    socket.open();
    await flush();
  });
  return { ...opened, view, socket };
}

// Delivers one frame on the chat socket and lets the view react to it.
export async function deliver(socket: FakeSocket, frame: unknown): Promise<void> {
  await act(async () => {
    socket.receive(frame);
    await flush();
  });
}
