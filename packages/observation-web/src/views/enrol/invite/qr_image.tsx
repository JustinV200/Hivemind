/**
 * `QrImage`: the invite link's QR code, drawn from the SVG the Entrance sends with the invite.
 *
 * The Entrance renders the link as an inline SVG (`InviteView.qr_svg`), so the page needs no QR
 * encoder of its own. The SVG goes to an `img` as a data URI rather than into the page's markup:
 * an image is never given a script's powers, so even a malformed answer cannot run anything. A
 * value that is not an SVG document is not drawn at all.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/enrol/invite/`. Drawn by `invite_card.tsx`.
 *
 * Key invariants:
 *   - The SVG is never injected as HTML.
 *   - The image has alternative text, and the link is also shown as text beside it.
 *
 * See Also:
 *   - docs/entrance/openapi.json, `InviteView`.
 */
import type { ReactElement } from 'react';

export function QrImage(props: { svg: string }): ReactElement | null {
  if (!props.svg.trimStart().startsWith('<svg')) return null;
  const source = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(props.svg)}`;
  return <img className="enrol-qr" src={source} alt="QR code for the invite link" />;
}
