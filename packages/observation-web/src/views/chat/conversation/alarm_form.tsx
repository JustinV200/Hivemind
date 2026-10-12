/**
 * `AlarmForm`: acknowledge an Alarm that reached the human.
 *
 * An Alarm the Queen could not settle on her own comes to the human as a chat line. This shows what
 * the inbox knows of it (severity and kind, in words) and one Acknowledge button that posts to
 * `POST /v1/inbox/alarms/{id}/acknowledge`. On success the slice drops the Alarm, so the form
 * disappears. A refusal is shown here, in place.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/chat/conversation/`. Drawn by `line_row.tsx` and
 *   `waiting.tsx`; calls `ChatActions.acknowledge`.
 *
 * Key invariants:
 *   - The button is disabled while the request is on its way.
 *   - Severity is always a word (INFO, WARNING, CRITICAL), never colour alone.
 *
 * See Also:
 *   - docs/entrance/landing-board.md section 8.3.
 */
import { useState } from 'react';
import type { ReactElement } from 'react';
import { ErrorNotice } from '../../../components';
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
        <span className="chat-tag chat-tag--alarm">{alarm.severity}</span> <code>{alarm.kind}</code>
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
