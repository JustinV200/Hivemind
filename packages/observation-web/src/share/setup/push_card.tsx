/**
 * The push card: one button that turns on push notices through the phone's distributor.
 *
 * Push on the web needs a click (the browser asks for permission from one), and the Android shell
 * keeps that rule: the card asks, the human taps, the distributor registers and the endpoint goes
 * to the Entrance as a Web Push subscription. It shows only while this device has no subscription
 * and the human has not said "Not now", and it names what is needed when it fails (no distributor
 * installed, permission refused).
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/share/`. Drawn by `phone_host.tsx` when the platform owns a
 *   distributor registration and a session is active.
 *
 * Key invariants:
 *   - Nothing registers without the tap; a refusal leaves the card up with the reason.
 *   - "Not now" is remembered in the key-value store so the card does not return every launch.
 *   - The card never sees an endpoint or key; the client registers them.
 *
 * See Also:
 *   - src/landing_board/client/push.ts and platform/android/push.ts.
 */
import { useEffect, useState } from 'react';
import type { ReactElement } from 'react';
import { ErrorNotice } from '../../components';
import type { KeyValueStore } from '../../landing_board/client/browser';
import { useHive } from '../../store';
import { localFailure, PUSH_CODE } from '../local_error';

const DECLINED = 'push_declined';

// The card itself: words, the refusal if there was one, and the two buttons.
function Offer(props: {
  busy: boolean;
  error: unknown;
  onTurnOn: () => void;
  onNotNow: () => void;
}): ReactElement {
  return (
    <section className="phone-card" aria-label="Push notices">
      <h2>Get a notice when something is waiting?</h2>
      <p className="phone-note">
        A notice says only that something is waiting, never what. It arrives through a UnifiedPush
        distributor you control, such as ntfy.
      </p>
      {props.error ? <ErrorNotice error={props.error} /> : null}
      <div className="phone-row">
        <button type="button" className="button" disabled={props.busy} onClick={props.onTurnOn}>
          {props.busy ? 'Setting up' : 'Turn on push notices'}
        </button>
        <button type="button" className="button" disabled={props.busy} onClick={props.onNotNow}>
          Not now
        </button>
      </div>
    </section>
  );
}

export function PushCard(props: { store: KeyValueStore }): ReactElement | null {
  const { store } = props;
  const { client } = useHive();
  // `unknown` until the store has been read, so the card never flashes for a registered device.
  const [wanted, setWanted] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    let live = true;
    void Promise.all([client.push.isRegistered(), store.get(DECLINED)]).then(
      ([registered, declined]) => {
        if (live) setWanted(!registered && declined !== true);
      },
    );
    return () => {
      live = false;
    };
  }, [client, store]);

  if (!wanted) return null;

  async function turnOn(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      await client.push.register();
      setWanted(false);
    } catch (failure) {
      setError(localFailure(PUSH_CODE, failure));
      setBusy(false);
    }
  }

  function notNow(): void {
    void store.put(DECLINED, true);
    setWanted(false);
  }

  return <Offer busy={busy} error={error} onTurnOn={() => void turnOn()} onNotNow={notNow} />;
}
