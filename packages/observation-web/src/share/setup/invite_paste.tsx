/**
 * The invite box: paste the invite link when the app does not know the Entrance's address yet.
 *
 * A phone learns the Entrance's address from the invite link (landing-board.md section 4). When the
 * link opens in the app, the address is adopted automatically; when Android does not verify the App
 * Link (a VPN-only Entrance the phone cannot reach yet), the link opens in the browser instead. This
 * box takes the same link pasted by hand, so the join screen can still run. It shows only until an
 * address is stored.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/share/`. Drawn by `phone_host.tsx` when the platform offers
 *   `InviteLinks` and none is configured.
 *
 * Key invariants:
 *   - Text that is not an invite link (or when a session exists) is refused with a sentence; the
 *     link itself is never logged or shown again.
 *   - On success the page reloads on the join screen, so no module keeps the old address.
 *
 * See Also:
 *   - src/landing_board/client/platform/android/invite.ts for what is accepted.
 */
import { useState } from 'react';
import type { FormEvent, ReactElement } from 'react';
import { ErrorNotice } from '../../components';
import type { InviteLinks } from '../../landing_board/client';
import { shareFailure } from '../local_error';

export function InvitePaste(props: { links: InviteLinks }): ReactElement {
  const [link, setLink] = useState('');
  const [error, setError] = useState<unknown>(null);

  async function submit(event: FormEvent): Promise<void> {
    event.preventDefault();
    const accepted = await props.links.accept(link);
    if (!accepted) setError(shareFailure('That is not an invite link for this device.'));
  }

  return (
    <form className="phone-card" aria-label="Find your Hive" onSubmit={(e) => void submit(e)}>
      <h2>Paste your invite link</h2>
      <p className="phone-note">
        This app does not know where your Hive is yet. Open the invite link from the operator, or
        paste it here.
      </p>
      <label>
        Invite link
        <input
          name="invite-link"
          inputMode="url"
          autoComplete="off"
          placeholder="https://hive.example/enrol#code=..."
          value={link}
          onChange={(event) => setLink(event.target.value)}
        />
      </label>
      {error ? <ErrorNotice error={error} /> : null}
      <button type="submit" className="button" disabled={link.trim() === ''}>
        Use this link
      </button>
    </form>
  );
}
