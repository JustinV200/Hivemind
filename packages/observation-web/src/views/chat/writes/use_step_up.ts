/**
 * `useStepUp()`: run a write, and when the Entrance asks for both factors again, ask and retry once.
 *
 * Some writes need a step-up: the Entrance answers `403` with `step_up_required` (landing-board.md
 * section 7). `guarded(action)` runs the action; on that refusal it shows the password prompt,
 * calls `session.stepUp(password)` (which also takes the device key through the passkey) and runs
 * the action exactly once more. A wrong password keeps the prompt open with the refusal shown; a
 * cancel gives the original refusal back to the caller. A second `step_up_required` after a good
 * step-up is returned as an error and never loops.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/chat/writes/`. Used by `use_actions.ts` around every
 *   write; the prompt it describes is drawn by `step_up_prompt.tsx`.
 *
 * Key invariants:
 *   - The password lives only in the prompt's input and in the one `stepUp` call; this hook never
 *     stores it.
 *   - A held request (the refusal carries a `pending_id`) is not retried: no person can step up
 *     for it on this device, so it is passed back as an error.
 *   - An action is retried at most once per refusal.
 *
 * See Also:
 *   - src/landing_board/client/session.ts `stepUp`.
 *   - docs/entrance/landing-board.md section 7.
 */
import { useCallback } from 'react';
import { LandingBoardError } from '../../../landing_board/client';
import { useHive } from '../../../store';
import { usePasswordAsk } from './use_password_ask';
import type { StepUpAsk } from './use_password_ask';

export const STEP_UP_CODE = 'hivemind.entrance.step_up_required';

export type { StepUpAsk };

export interface StepUp {
  guarded<T>(action: () => Promise<T>): Promise<T>;
  ask: StepUpAsk | null;
  submit(password: string): void;
  cancel(): void;
}

export function needsStepUp(error: unknown): boolean {
  return error instanceof LandingBoardError && error.status === 403 && error.code === STEP_UP_CODE;
}

// A refusal the interactive device can answer itself, as opposed to a held request.
function canStepUp(error: unknown): boolean {
  return needsStepUp(error) && error instanceof LandingBoardError && error.pendingId === null;
}

export function useStepUp(): StepUp {
  const { client } = useHive();
  const { ask, request, submit, cancel, close } = usePasswordAsk();

  // Asks until a password is accepted; a cancel gives the original refusal back.
  const confirm = useCallback(
    async (original: unknown) => {
      let failure: unknown = null;
      try {
        for (;;) {
          const typed = await request(original, failure);
          if (typed === null) throw original;
          try {
            // Latency: a passkey prompt and one round trip; a wrong password rejects.
            await client.session.stepUp(typed);
            return;
          } catch (error) {
            failure = error;
          }
        }
      } finally {
        close();
      }
    },
    [client, request, close],
  );

  const guarded = useCallback(
    async <T>(action: () => Promise<T>): Promise<T> => {
      try {
        return await action();
      } catch (error) {
        if (!canStepUp(error)) throw error;
        await confirm(error);
        // The one retry: a second refusal now is the answer, not a reason to ask again.
        return action();
      }
    },
    [confirm],
  );

  return { guarded, ask, submit, cancel };
}
