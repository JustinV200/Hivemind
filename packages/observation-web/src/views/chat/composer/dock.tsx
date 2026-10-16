/**
 * `Dock`: the strip that sticks to the bottom of the Chat view: step-up prompt, spoken card, composer.
 *
 * What the human may need to act on right now lives here, nearest the thumb: the password prompt
 * when the Entrance asked for both factors again, the transcript card of what was just spoken, and
 * the composer itself. On a phone it sits above the nav; the conversation scrolls above it.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/chat/composer/`. Drawn by `chat_screen.tsx` unless the
 *   device may not submit; a step-up refusal keeps it, because the prompt that answers it is here.
 *
 * Key invariants:
 *   - The prompt is keyed by its round, so a wrong password gives a clean field.
 *   - Dismissing the spoken card only clears it from the slice; the goal it names is unchanged.
 *
 * See Also:
 *   - src/views/chat/composer/composer.tsx and src/views/chat/voice/spoken_card.tsx.
 */
import type { ReactElement } from 'react';
import { useHive } from '../../../store';
import type { StepUp } from '../../devices';
import type { ChatState } from '../slice';
import { SpokenCard } from '../voice/spoken_card';
import { StepUpPrompt } from '../writes/step_up_prompt';
import type { ChatActions } from '../writes/use_actions';
import { Composer } from './composer';

export function Dock(props: {
  state: ChatState;
  actions: ChatActions;
  stepUp: StepUp;
}): ReactElement {
  const { state, actions, stepUp } = props;
  const { store } = useHive();
  return (
    <div className="chat-dock">
      {stepUp.ask ? (
        <StepUpPrompt
          key={stepUp.ask.round}
          ask={stepUp.ask}
          onSubmit={stepUp.submit}
          onCancel={stepUp.cancel}
        />
      ) : null}
      <SpokenCard
        state={state}
        actions={actions}
        onDismiss={() => store.dispatch({ slice: 'chat', event: { type: 'dismiss_spoken' } })}
      />
      <Composer
        actions={actions}
        questions={state.inbox?.questions ?? []}
        guarded={stepUp.guarded}
      />
    </div>
  );
}
