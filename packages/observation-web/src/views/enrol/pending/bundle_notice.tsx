/**
 * `BundleNotice`: the sealed certificate bundle an approval issued, with its passphrase, once.
 *
 * When the Hive runs its own certificate authority, approving a browser's passkey device also seals
 * a client certificate bundle (a PKCS#12 file) for mutual TLS. The Entrance answers with it once and
 * stores it nowhere, so this notice is the only chance to take it: a download link for the file and
 * the passphrase the operator types on the device when importing it. Dismissing it drops both from
 * memory.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/enrol/pending/`. Drawn by `enrol_screen.tsx` while the
 *   action hook holds a bundle.
 *
 * Key invariants:
 *   - The passphrase is shown as text only while the notice is open and is never stored.
 *   - The file is offered as a data link, so no request is made to fetch it.
 *
 * See Also:
 *   - docs/entrance/openapi.json, `BundleView` and `ApprovedDeviceView`.
 */
import type { ReactElement } from 'react';
import type { IssuedBundle } from '../use_enrol_actions';

export function BundleNotice(props: { bundle: IssuedBundle; onDismiss: () => void }): ReactElement {
  const { bundle } = props;
  return (
    <section className="enrol-bundle" aria-label="Certificate bundle" role="status">
      <h3>Certificate for {bundle.device}</h3>
      <p>
        Take this now: the Hive does not keep it. Import the file on the device and type the
        passphrase when asked.
      </p>
      <p>
        Passphrase <code>{bundle.passphrase}</code>
      </p>
      <div className="enrol-row">
        <a
          className="button"
          download={`${bundle.device}.p12`}
          href={`data:application/x-pkcs12;base64,${bundle.pkcs12_base64}`}
        >
          Save the file
        </a>
        <button type="button" className="button" onClick={props.onDismiss}>
          I have it
        </button>
      </div>
    </section>
  );
}
