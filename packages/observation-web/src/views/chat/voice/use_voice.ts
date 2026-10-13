/**
 * `useVoice()`: push-to-talk for the composer: the hold, plus the choice of what is being said.
 *
 * It joins the hold (`use_hold.ts`) to the chat's writes: the clip recorded while the button is held
 * goes to `ChatActions.speak` with the chosen intent. The choice is a task (echoed back for
 * confirmation), a message, or the answer to one of the questions waiting; a choice whose question
 * has gone (answered on another device) falls back to a task.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/chat/voice/`. Called by `composer.tsx`; records through
 *   `client.http.recorder` and sends through `ChatActions.speak`.
 *
 * Key invariants:
 *   - The intent used is the one chosen at the moment the clip is sent.
 *   - Nothing here touches `MediaRecorder` or the network itself.
 *
 * See Also:
 *   - src/views/chat/voice/use_hold.ts for the recording rules.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useHive } from '../../../store';
import type { QuestionView } from '../slice';
import type { ChatActions, Clip } from '../writes/use_actions';
import { useHold } from './use_hold';
import type { Hold } from './use_hold';

export interface IntentOption {
  value: string;
  label: string;
}

export interface Voice extends Hold {
  intent: string;
  options: readonly IntentOption[];
  setIntent(value: string): void;
}

const SHORT = 40; // Characters of a question shown in the "Answer" choice.

function optionsFor(questions: readonly QuestionView[]): IntentOption[] {
  return [
    { value: 'goal', label: 'A task' },
    { value: 'chat', label: 'A message' },
    ...questions.map((question) => ({
      value: `answer:${question.id}`,
      label: `Answer: ${question.text.slice(0, SHORT)}`,
    })),
  ];
}

export function useVoice(actions: ChatActions, questions: readonly QuestionView[]): Voice {
  const { client } = useHive();
  const [chosen, setChosen] = useState('goal');
  const options = optionsFor(questions);
  const intent = options.some((option) => option.value === chosen) ? chosen : 'goal';
  // The send reads the choice from the ref, so a re-render during a hold cannot change it.
  const current = useRef(intent);
  useEffect(() => {
    current.current = intent;
  }, [intent]);

  const send = useCallback((clip: Clip) => actions.speak(clip, current.current), [actions]);
  const hold = useHold(client.http.recorder, send);
  return { ...hold, intent, options, setIntent: setChosen };
}
