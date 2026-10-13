/**
 * The confirmation card: what is about to be sent to the Hive, and a button to send it.
 *
 * Another app shared something. Before anything is posted the human sees what it is (the text
 * itself, or the kind, type and size of an image or a clip) and where it will go (the Hive at the
 * stored base URL), and taps Send or Cancel. A refusal comes back in place; a step-up is asked for
 * in place too. After a successful send the app goes to the Chat view, where the line, the
 * transcript or the Queen's reply appears.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/share/`. Drawn by `phone_host.tsx` while an item is held and a
 *   session is active.
 *
 * Key invariants:
 *   - Nothing is sent without the Send tap, and Send is disabled while a send is in flight.
 *   - The destination shown is the origin requests actually go to.
 *   - Colour is never the only signal; every state is a word.
 *
 * See Also:
 *   - src/share/send_item.ts for the routes, and docs/adr/0047 for the intake rules.
 */
import { useState } from 'react';
import type { ReactElement } from 'react';
import { ErrorNotice } from '../../components';
import type { SharedFile, SharedItem } from '../../landing_board/client';
import { hrefFor } from '../../router';
import { useHive } from '../../store';
import { StepUpPrompt, useChatActions } from '../../views/chat';
import { measureClip } from './clip_length';
import { localFailure, SHARE_CODE } from '../local_error';
import { describeItem, PREVIEW_CHARS } from './media';
import { sendShared } from './send_item';

export function ShareCard(props: {
  item: SharedItem;
  origin: string;
  onFinish: () => void;
  // Reads a clip's length; the WebView's by default, a test passes its own (jsdom cannot decode).
  measure?: (clip: SharedFile) => Promise<number>;
}): ReactElement {
  const {
    item,
    origin,
    onFinish,
    measure = (clip) => measureClip(clip.bytes, clip.mediaType),
  } = props;
  const { client } = useHive();
  const { actions, stepUp } = useChatActions();
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function send(): Promise<void> {
    setSending(true);
    setError(null);
    try {
      await sendShared(item, {
        actions,
        guarded: stepUp.guarded,
        http: client.http,
        measure,
      });
      onFinish();
      window.location.hash = hrefFor({ name: 'chat' });
    } catch (failure) {
      setError(localFailure(SHARE_CODE, failure));
      setSending(false);
    }
  }

  return (
    <section className="phone-card" role="dialog" aria-label="Shared with the Hive">
      <h2>Send to the Hive?</h2>
      <p>{describeItem(item)}</p>
      {item.kind === 'text' ? (
        <blockquote className="phone-preview">{item.text.slice(0, PREVIEW_CHARS)}</blockquote>
      ) : null}
      <p className="phone-note">
        To the Hive at <code>{origin}</code>. Nothing is sent until you tap Send.
      </p>
      {stepUp.ask ? (
        <StepUpPrompt
          key={stepUp.ask.round}
          ask={stepUp.ask}
          onSubmit={stepUp.submit}
          onCancel={stepUp.cancel}
        />
      ) : null}
      {error ? <ErrorNotice error={error} /> : null}
      <div className="phone-row">
        {item.kind === 'refused' ? null : (
          <button type="button" className="button" disabled={sending} onClick={() => void send()}>
            {sending ? 'Sending' : 'Send'}
          </button>
        )}
        <button type="button" className="button" disabled={sending} onClick={onFinish}>
          {item.kind === 'refused' ? 'Dismiss' : 'Cancel'}
        </button>
      </div>
    </section>
  );
}
