/**
 * `AlarmForm`: acknowledge an Alarm that reached the human.
 *
 * An Alarm the Queen could not settle on her own comes to the human as a chat line. This shows what
 * the inbox knows of it (severity, and the kind as a plain phrase with the code in its `title`)
 * and one Acknowledge button that posts to
 * `POST /v1/inbox/alarms/{id}/acknowledge`. On success the slice drops the Alarm, so the form
 * disappears. A refusal is shown here, in place.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/chat/conversation/`. Drawn by `line_row.tsx` and
 *   `waiting.tsx`; calls `ChatActions.acknowledge`.
 *
 * Key invariants:
 *   - The button is disabled while the request is on its way.
 *   - Severity is always a word (`info`, `warning`, `critical`, the code on its `title`), never
 *     colour alone.
 *
 * See Also:
 *   - docs/entrance/landing-board.md section 8.3.
 */
import { useState } from 'react';
import type { ReactElement } from 'react';
import { alarmKindWords, ErrorNotice, plainCode } from '../../../components';
import type { AlarmView } from '../slice';
import type { ChatActions } from '../writes/use_actions';

export function AlarmForm(props: { alarm: AlarmView; actions: ChatActions }): ReactElement {
  const { alarm, actions } = props;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function acknowledge(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      await actions.acknowledge(alarm.id);
    } catch (failure) {
      // The Alarm is still open: show why and let the human try again.
      setError(failure);
      setBusy(false);
    }
  }

  return (
    <div className="chat-form">
      <p className="chat-alarm-facts">
        <span className="tag tag--status tag--alarm" title={alarm.severity}>
          {plainCode(alarm.severity)}
        </span>{' '}
        <span title={alarm.kind}>{alarmKindWords(alarm.kind)}</span>
      </p>
      <div className="chat-row">
        <button type="button" className="button" disabled={busy} onClick={() => void acknowledge()}>
          Acknowledge
        </button>
      </div>
      {error ? <ErrorNotice error={error} /> : null}
    </div>
  );
}
