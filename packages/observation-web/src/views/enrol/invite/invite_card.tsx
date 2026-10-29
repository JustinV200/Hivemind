/**
 * `InviteCard`: the invite just minted, as a code, a link, a QR code and its expiry.
 *
 * The operator hands the invite to a new device three ways: reads out the code (groups of four so
 * it can be said aloud), copies or shares the link, or lets the device scan the QR code; the card
 * says so, and what happens next, since a code, a link and a QR alone say nothing of what to do
 * with them. The invite
 * works once and lapses at the time shown; after that the card says so instead of leaving a dead
 * code on the screen. Withdrawing it deletes the unredeemed invite on the Entrance.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/enrol/invite/`. Drawn by `invite_panel.tsx` on the Hive
 *   Stand only; Withdraw calls `actions.withdraw`.
 *
 * Key invariants:
 *   - The code is shown as text and never written to storage or the console.
 *   - Copy uses the clipboard when the browser allows it; the link is selectable text either way.
 *   - Expiry is shown as a time and as a word once it has passed.
 *
 * See Also:
 *   - docs/entrance/landing-board.md section 4 for the code and the link.
 */
import { useEffect, useState } from 'react';
import type { ReactElement } from 'react';
import { TimeStamp } from '../../../components';
import type { EnrolActions } from '../use_enrol_actions';
import type { InviteRecord } from '../slice';
import { QrImage } from './qr_image';

// The longest a browser timer can wait; a longer one fires at once.
const MAX_TIMER_MS = 2 ** 31 - 1;

// True once the invite's expiry has passed; flips by itself when the moment arrives.
function useLapsed(expiresAt: string): boolean {
  const [now, setNow] = useState(() => Date.now());
  const end = Date.parse(expiresAt);
  useEffect(() => {
    const wait = end - Date.now();
    if (!(wait > 0)) return;
    // One timer to the moment of expiry, not a ticking clock.
    const timer = window.setTimeout(() => setNow(Date.now()), Math.min(wait + 1, MAX_TIMER_MS));
    return () => window.clearTimeout(timer);
  }, [end]);
  return Number.isFinite(end) && now >= end;
}

function CopyLink(props: { link: string }): ReactElement {
  const [copied, setCopied] = useState(false);
  const copy = (): void => {
    // The clipboard is absent in an insecure context; the link stays selectable below.
    void navigator.clipboard
      ?.writeText(props.link)
      .then(() => setCopied(true))
      .catch(() => setCopied(false));
  };
  return (
    <button type="button" className="button" onClick={copy}>
      {copied ? 'Copied' : 'Copy link'}
    </button>
  );
}

export function InviteCard(props: { invite: InviteRecord; actions: EnrolActions }): ReactElement {
  const { invite, actions } = props;
  const lapsed = useLapsed(invite.expires_at);
  return (
    <section className="enrol-invite comb-cell comb-cell--strong" aria-label="Invite">
      <h3>Invite</h3>
      {lapsed ? (
        <p className="enrol-warn" role="status">
          This invite has expired. Withdraw it and create a new one.
        </p>
      ) : (
        <p className="enrol-note">
          Open this link on the new device, or scan the code; it works once, until{' '}
          <TimeStamp at={invite.expires_at} />. The device then appears below, waiting for your
          approval.
        </p>
      )}
      <p className="enrol-code" aria-label="Invite code">
        {invite.code}
      </p>
      <p className="enrol-link">
        <code>{invite.url}</code>
      </p>
      <QrImage svg={invite.qr_svg} />
      <div className="enrol-row">
        <CopyLink link={invite.url} />
        <button
          type="button"
          className="button"
          disabled={actions.busy}
          onClick={() => void actions.withdraw(invite.device_id)}
        >
          Withdraw invite
        </button>
      </div>
    </section>
  );
}
