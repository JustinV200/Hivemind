/**
 * `useStepUp()`: run a write, and when the Entrance wants both factors again, ask and retry once.
 *
 * Some writes need a step-up: the Entrance answers `403` with `step_up_required` (landing-board.md
 * section 7). `guarded(action)` runs the action; on that refusal it opens the prompt, calls
 * `session.stepUp(password)` (which also takes the device key through the passkey) and runs the
 * action exactly once more. A wrong password keeps the prompt open with the refusal shown; a cancel
 * hands the original refusal back to the caller. A second `step_up_required` after a good step-up
 * is the answer and never loops. A refusal that carries a `pending_id` is a held request from a
 * device no person types at, which this device cannot step up for, so it is passed back untouched.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/devices/step_up/`. Shared by the Devices and Enrol views
 *   (the chat keeps its own); the prompt it describes is drawn by `step_up_prompt.tsx`.
 *
 * Key invariants:
 *   - The password lives only in the prompt's input and in the one `stepUp` call; it is never kept.
 *   - At most one question is open; leaving the view answers an open one with a cancel.
 *   - An action is retried at most once per refusal.
 *   - A good step-up clears the session slice's `needsStepUp`, so the shell's banner goes away.
 *
 * See Also:
 *   - src/landing_board/client/session.ts `stepUp`.
 *   - docs/entrance/landing-board.md section 7.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { LandingBoardError } from '../../../landing_board/client';
import { useHive } from '../../../store';

export const STEP_UP_CODE = 'hivemind.entrance.step_up_required';

// What the prompt shows: the refusal that asked, and the failure of the last password tried.
export interface StepUpAsk {
  original: unknown;
  failure: unknown;
  // Changes on every ask, so the prompt remounts with a clean input after a wrong password.
  round: number;
}

export interface StepUp {
  guarded<T>(action: () => Promise<T>): Promise<T>;
  ask: StepUpAsk | null;
  submit(password: string): void;
  cancel(): void;
}

// A refusal this interactive device can answer itself, as opposed to a held request.
function canStepUp(error: unknown): boolean {
  return (
    error instanceof LandingBoardError &&
    error.status === 403 &&
    error.code === STEP_UP_CODE &&
    error.pendingId === null
  );
}

interface Question {
  ask: StepUpAsk | null;
  // Opens a question and resolves with the typed password, or null when the human cancels.
  request(original: unknown, failure: unknown): Promise<string | null>;
  settle(password: string | null): void;
  close(): void;
}

// The open question "what is your password?" as a promise the flow below can await.
function useQuestion(): Question {
  const [ask, setAsk] = useState<StepUpAsk | null>(null);
  const resolver = useRef<((password: string | null) => void) | null>(null);
  const rounds = useRef(0);

  const settle = useCallback((password: string | null) => {
    const resolve = resolver.current;
    resolver.current = null;
    resolve?.(password);
  }, []);

  // Leaving the view answers an open question with a cancel, so nothing waits forever.
  useEffect(() => () => settle(null), [settle]);

  const request = useCallback(
    (original: unknown, failure: unknown) =>
      new Promise<string | null>((resolve) => {
        resolver.current = resolve;
        rounds.current += 1;
        setAsk({ original, failure, round: rounds.current });
      }),
    [],
  );
  const close = useCallback(() => setAsk(null), []);
  return { ask, request, settle, close };
}

export function useStepUp(): StepUp {
  const { client, store } = useHive();
  const { ask, request, settle, close } = useQuestion();

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
            store.dispatch({ slice: 'session', event: { type: 'stepped_up' } });
            return;
          } catch (error) {
            failure = error;
          }
        }
      } finally {
        close();
      }
    },
    [client, store, request, close],
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

  return { guarded, ask, submit: settle, cancel: () => settle(null) };
}
