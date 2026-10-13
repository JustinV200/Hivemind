/**
 * `PushToTalk`: the hold-to-record button, its countdown and the choice of what is being said.
 *
 * Hold the button (pointer or Space and Enter) to record, release to send. While recording it
 * shows the seconds left of the cap. A select chooses whether the clip is a task (echoed back for
 * confirmation), a message, or the answer to one of the questions waiting. A device with no
 * recorder shows nothing at all.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/chat/voice/`. Drawn by `composer.tsx`; all behaviour is
 *   in `use_voice.ts`, this only maps events to it.
 *
 * Key invariants:
 *   - Releasing in any way (pointer up, cancel, leave, key up) ends the hold; holding by key
 *     ignores auto-repeat so it starts once.
 *   - Colour is never the only signal: the button's words change with the phase.
 *
 * See Also:
 *   - docs/observation/design.md sections 5.6 and 7.
 */
import type { KeyboardEvent, ReactElement } from 'react';
import { ErrorNotice } from '../../../components';
import type { Voice } from './use_voice';

const LABELS = {
  idle: 'Hold to talk',
  starting: 'Starting the microphone',
  recording: 'Recording, release to send',
  sending: 'Sending',
} as const;

const isHoldKey = (event: KeyboardEvent): boolean => event.key === ' ' || event.key === 'Enter';

export function PushToTalk(props: { voice: Voice }): ReactElement | null {
  const { voice } = props;
  if (!voice.available) return null;
  const recording = voice.phase === 'recording';
  return (
    <div className="chat-voice">
      <label className="chat-voice-intent">
        Speak as
        <select value={voice.intent} onChange={(event) => voice.setIntent(event.target.value)}>
          {voice.options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>
      <button
        type="button"
        className="button chat-hold"
        aria-pressed={recording}
        disabled={voice.phase === 'sending'}
        onPointerDown={voice.begin}
        onPointerUp={voice.end}
        onPointerCancel={voice.end}
        onPointerLeave={voice.end}
        onKeyDown={(event) => {
          if (isHoldKey(event) && !event.repeat) voice.begin();
        }}
        onKeyUp={(event) => {
          if (isHoldKey(event)) voice.end();
        }}
        onContextMenu={(event) => event.preventDefault()}
      >
        {LABELS[voice.phase]}
      </button>
      {recording ? (
        <span className="chat-countdown" role="timer">
          {voice.secondsLeft} s left
        </span>
      ) : null}
      {voice.hint ? <p className="chat-hint">{voice.hint}</p> : null}
      {voice.error ? <ErrorNotice error={voice.error} /> : null}
    </div>
  );
}
