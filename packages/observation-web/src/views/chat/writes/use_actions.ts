/**
 * `useChatActions()`: every write the Chat view can make, as one set of functions.
 *
 * The chat is the only write path of the Observation Hive (codingrules 8.11), and these are its
 * only writes: a message to the Queen (`POST /v1/chat`), a clip (`POST /v1/chat/audio`), an answer
 * and an acknowledgement (the two inbox routes) and the confirm or decline of a spoken goal. Each
 * runs through `guarded` so a step-up is asked for and retried once, and each puts its result in
 * the slice (the Entrance withdraws an answered question on every other device, and this one drops
 * it at once). A message the human sends is shown as pending until its own line arrives.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/chat/writes/`. Called by `chat_screen.tsx`, which hands
 *   the set to the composer, the question and Alarm forms and the spoken card.
 *
 * Key invariants:
 *   - These are the only requests the view makes besides its reads; a test lists them.
 *   - `send` never throws (a failure becomes a failed pending message); the others throw so the
 *     form that called them can show the refusal in place.
 *   - A `409` on an answer or acknowledgement means someone got there first: the item is removed
 *     and a one-line notice says so, rather than an error.
 *
 * See Also:
 *   - src/views/chat/writes/use_step_up.ts for the step-up flow.
 *   - docs/entrance/landing-board.md sections 8.1, 8.3 and 8.4.
 */
import { useMemo, useRef } from 'react';
import { LandingBoardError } from '../../../landing_board/client';
import type { HttpClient } from '../../../landing_board/client';
import { useHive } from '../../../store';
import type { QuestionView } from '../slice';
import { useStepUp } from './use_step_up';
import type { StepUp } from './use_step_up';

export type Clip = Parameters<HttpClient['postAudio']>[0];
export type Intent = Parameters<HttpClient['postAudio']>[1];

export interface ChatActions {
  send(text: string): Promise<void>;
  answer(question: QuestionView, text: string, option: number | null): Promise<void>;
  acknowledge(alarmId: string): Promise<void>;
  decide(requestId: string, verdict: 'confirm' | 'decline'): Promise<void>;
  speak(clip: Clip, intent: Intent): Promise<void>;
}

// The Entrance's own default reason for a decline, which the document asks the client to send.
const DECLINED = 'declined by the human';
const ALREADY_DONE = 'That was already dealt with on another device.';

const isConflict = (error: unknown): boolean =>
  error instanceof LandingBoardError && error.status === 409;

export function useChatActions(): { actions: ChatActions; stepUp: StepUp } {
  const { client, store } = useHive();
  const stepUp = useStepUp();
  const { guarded } = stepUp;
  const counter = useRef(0);

  const actions = useMemo<ChatActions>(() => {
    const { http } = client;
    const dispatch = store.dispatch;
    const now = (): string => new Date().toISOString();

    // Runs a write that may find its target already gone, and tidies the slice either way.
    async function settleItem(write: () => Promise<unknown>, done: () => void): Promise<void> {
      try {
        await guarded(write);
      } catch (error) {
        if (!isConflict(error)) throw error;
        dispatch({ slice: 'chat', event: { type: 'notice', text: ALREADY_DONE } });
      }
      done();
    }

    return {
      async send(text) {
        counter.current += 1;
        const key = `local:${Date.now()}:${counter.current}`;
        dispatch({ slice: 'chat', event: { type: 'compose', key, text, at: now() } });
        try {
          const accepted = await guarded(() => http.call('post', '/v1/chat', { body: { text } }));
          dispatch({ slice: 'chat', event: { type: 'sent', key, id: accepted.id } });
        } catch (error) {
          dispatch({ slice: 'chat', event: { type: 'send_failed', key, error } });
        }
      },
      answer: (question, text, option) =>
        settleItem(
          () =>
            http.call('post', '/v1/inbox/questions/{question_id}/answer', {
              params: { question_id: question.id },
              body: option === null ? { text } : { text, chosen_option: option },
            }),
          () => dispatch({ slice: 'chat', event: { type: 'answered', questionId: question.id } }),
        ),
      acknowledge: (alarmId) =>
        settleItem(
          () =>
            http.call('post', '/v1/inbox/alarms/{alarm_id}/acknowledge', {
              params: { alarm_id: alarmId },
            }),
          () => dispatch({ slice: 'chat', event: { type: 'acknowledged', alarmId } }),
        ),
      async decide(requestId, verdict) {
        const params = { request_id: requestId };
        const goal = await guarded(() =>
          verdict === 'confirm'
            ? http.call('post', '/v1/goals/{request_id}/confirm', { params })
            : http.call('post', '/v1/goals/{request_id}/decline', {
                params,
                body: { reason: DECLINED },
              }),
        );
        dispatch({ slice: 'chat', event: { type: 'goal', goal } });
      },
      async speak(clip, intent) {
        const result = await guarded(() => http.postAudio(clip, intent));
        dispatch({ slice: 'chat', event: { type: 'spoken', result, at: now() } });
      },
    };
  }, [client, store, guarded]);

  return { actions, stepUp };
}
