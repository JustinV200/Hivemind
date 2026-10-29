/**
 * Make a refusal made on this device show the way the Entrance's refusals do.
 *
 * `ErrorNotice` draws a `LandingBoardError`: the sentence, then what to do. A failure that never
 * reached the Entrance (an unreadable clip, a distributor that is not installed) is a plain
 * `Error`, which `ErrorNotice` would describe as a network failure. These helpers wrap such a
 * failure in a `LandingBoardError` with status 0, which is how the client marks a refusal made
 * locally, and keep the sentence. An error that already is a `LandingBoardError` passes through
 * untouched.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/share/`. Used by `send_item.ts`, `share_card.tsx` and
 *   `push_card.tsx`.
 *
 * Key invariants:
 *   - Status 0 and a `hivemind.client.` code mark the failure as local, never as the Entrance's.
 *   - The sentence is the error's own message; no stack or native detail is added.
 *
 * See Also:
 *   - src/landing_board/client/errors.ts for `LandingBoardError` and `describeError`.
 */
import { LandingBoardError } from '../landing_board/client';

export const SHARE_CODE = 'hivemind.client.share';
export const PUSH_CODE = 'hivemind.client.push';

export function localFailure(code: string, error: unknown): LandingBoardError {
  if (error instanceof LandingBoardError) return error;
  const detail = error instanceof Error ? error.message : 'That did not work.';
  return new LandingBoardError(0, { error: code, detail });
}

export function shareFailure(detail: string): LandingBoardError {
  return new LandingBoardError(0, { error: SHARE_CODE, detail });
}
