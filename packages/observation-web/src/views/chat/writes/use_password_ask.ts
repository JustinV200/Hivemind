/**
 * `usePasswordAsk()`: the open question "what is your password?" as a promise a flow can await.
 *
 * The step-up flow is written as plain `await` code, but the answer comes from a form the human
 * fills in. This hook bridges the two: `request` shows the prompt (state for the view to draw) and
 * resolves with the typed password, or with null when the human cancels. Each request carries the
 * refusal that started the flow and the failure of the last password, and a round number that
 * changes every time so the prompt remounts clean.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/chat/writes/`. Used by `use_step_up.ts`; the state it
 *   returns is drawn by `step_up_prompt.tsx`.
 *
 * Key invariants:
 *   - At most one request is open; a new request replaces the resolver of an unanswered one.
 *   - Leaving the view answers an open request with a cancel, so nothing waits forever.
 *   - The password is handed to the resolver and not kept.
 *
 * See Also:
 *   - src/views/chat/writes/use_step_up.ts for the flow that awaits it.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

// What the prompt shows: the refusal that asked, and the failure of the last password tried.
export interface StepUpAsk {
  original: unknown;
  failure: unknown;
  // Changes on every ask, so the prompt remounts with a clean input after a wrong password.
  round: number;
}

export interface PasswordAsk {
  ask: StepUpAsk | null;
  request(original: unknown, failure: unknown): Promise<string | null>;
  submit(password: string): void;
  cancel(): void;
  close(): void;
}

export function usePasswordAsk(): PasswordAsk {
  const [ask, setAsk] = useState<StepUpAsk | null>(null);
  const resolver = useRef<((password: string | null) => void) | null>(null);
  const rounds = useRef(0);

  const settle = useCallback((password: string | null) => {
    const resolve = resolver.current;
    resolver.current = null;
    resolve?.(password);
  }, []);

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

  return {
    ask,
    request,
    submit: settle,
    cancel: () => settle(null),
    close: () => setAsk(null),
  };
}
