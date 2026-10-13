/**
 * The chat stream's wiring: its path, how a frame becomes a slice event and where it resumes.
 *
 * `/v1/chat/stream` sends one `ChatFrame` per new line, and the line's `seq` is the cursor. When
 * the Entrance closes the socket with 4409 (fell behind) the client reconnects with `?after=<seq>`
 * so nothing is missed. These are module-level values on purpose: `useViewStream` reopens the
 * stream whenever the identity of its arguments changes.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/chat/reads/`. Passed to `useViewStream` by
 *   `chat_screen.tsx`; the frame handler is the slice's `frame` event.
 *
 * Key invariants:
 *   - The cursor is read only from a chat frame; any other frame leaves it where it was.
 *   - `resume.query` builds exactly the `?after=` the document describes.
 *
 * See Also:
 *   - docs/entrance/landing-board.md section 8.2 and openapi.json `x-hive-streams`.
 *   - src/landing_board/client/socket.ts for the reconnect policy that calls these.
 */
import type { ChatEvent, ChatFrame } from '../slice';

export const CHAT_STREAM = '/v1/chat/stream';

// Each frame is folded at once; the reducer ignores anything that is not a chat line.
export const toChatEvent = (frame: ChatFrame): ChatEvent => ({ type: 'frame', frame });

export const CHAT_RESUME = {
  cursorOf: (frame: ChatFrame): string | null =>
    frame.type === 'chat' && frame.entry ? String(frame.entry.seq) : null,
  query: (cursor: string): string => `?after=${cursor}`,
};
