/**
 * `useGoal(guarded)`: ask the Queen for a task by posting the typed words as a goal.
 *
 * A chat message is read by the Queen and answered; it never starts a task by itself. A goal does:
 * `POST /v1/goals` commits a goal request (`202`) that the Queen plans on her own tick. This hook
 * posts the words with the documented defaults for everything else (the C1 ceiling, no budget of
 * its own, no tier), runs the post through `guarded` so a `403 step_up_required` asks for the
 * password and retries once, and puts the answer in the slice: a line in the conversation with
 * the request's id, and, when the Entrance holds it for the human's yes, the Confirm and Decline
 * controls. A refusal is returned to the composer, which keeps the words and shows it.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/chat/writes/`. Called by `composer/composer.tsx` with
 *   the chat's own `guarded` (from the Devices view's `step_up/`); the line is drawn by `conversation/goal_row`.
 *
 * Key invariants:
 *   - The only request here is `POST /v1/goals`; confirm and decline stay in `use_actions.ts`.
 *   - It never throws: a failure is `error`, and the next request clears it.
 *   - One request at a time: `request` while `busy` does nothing.
 *   - The text is posted as typed, trimmed; nothing about it is stored beyond the slice's line.
 *
 * See Also:
 *   - docs/entrance/landing-board.md section 8.3 (goals) and src/views/chat/voice/goal_controls.tsx.
 */
import { useCallback, useState } from 'react';
import type { components } from '../../../landing_board/types';
import { useHive } from '../../../store';
import type { StepUp } from '../../devices';

type GoalView = components['schemas']['GoalView'];
type Accepted = components['schemas']['GoalAccepted'];
type Submission = components['schemas']['GoalSubmission'];

// The schema's default ceiling, spelled out because the generated type requires the member.
const DEFAULT_CLEARANCE: Submission['clearance'] = 'C1';

export interface GoalRequest {
  request(text: string): Promise<boolean>;
  busy: boolean;
  error: unknown;
}

// What the slice knows of a request the Entrance just accepted; later reads would refine it.
export function viewOfAccepted(accepted: Accepted, at: string): GoalView {
  return {
    budget_usd: null,
    clearance: DEFAULT_CLEARANCE,
    comb_shield: null,
    confirmed_at: null,
    finished_at: null,
    goal_id: null,
    id: accepted.id,
    needs_confirmation: accepted.state === 'AWAITING_CONFIRMATION',
    received_at: at,
    refused: accepted.state === 'REFUSED',
    source: 'typed',
    state: accepted.state,
    updated_at: at,
  };
}

export function useGoal(guarded: StepUp['guarded']): GoalRequest {
  const { client, store } = useHive();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const request = useCallback(
    async (text: string): Promise<boolean> => {
      if (busy) return false;
      setBusy(true);
      setError(null);
      try {
        const body: Submission = { text, clearance: DEFAULT_CLEARANCE };
        // Latency: one round trip; the Entrance answers once the request is committed.
        const accepted = await guarded(() => client.http.call('post', '/v1/goals', { body }));
        const at = new Date().toISOString();
        const goal = viewOfAccepted(accepted, at);
        store.dispatch({ slice: 'chat', event: { type: 'goal_asked', goal, text, at } });
        return true;
      } catch (failure) {
        setError(failure);
        return false;
      } finally {
        setBusy(false);
      }
    },
    [busy, client, store, guarded],
  );

  return { request, busy, error };
}
