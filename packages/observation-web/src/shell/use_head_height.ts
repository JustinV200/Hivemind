/**
 * `useHeadHeight()`: keep `--head-h` on the shell equal to the sticky head's measured height.
 *
 * The nav rail is sticky under the head and sized to the rest of the viewport, and the Cell page
 * header and the Honey reading pane stick under the head too. The head's height is not fixed: the
 * step-up banner and the notice bar appear and go, and the notice bar wraps on a narrow rail. A
 * stylesheet cannot read it, so the rail once started at the bar plus the strip and covered the
 * notice bar's first words ("a question is waiting" is the shell's most important line). This
 * hook watches the head with a `ResizeObserver` and writes its height to `--head-h` on the shell,
 * which `shell.css`, `cell.css` and `honey.css` read; `tokens.css` gives the variable its default
 * (the bar plus the strip) for the moment before the first measurement.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/shell/`. Used by `shell.tsx`.
 *
 * Key invariants:
 *   - Writes one custom property on the shell element and removes it on unmount; nothing else.
 *   - Without `ResizeObserver` (jsdom, an old browser) it does nothing and the default stands.
 *
 * See Also:
 *   - docs/observation/design.md section 2 (the head never hides, the rail sits under it).
 */
import { useLayoutEffect } from 'react';
import type { RefObject } from 'react';

export const HEAD_HEIGHT_VAR = '--head-h';

export function useHeadHeight(
  shell: RefObject<HTMLElement | null>,
  head: RefObject<HTMLElement | null>,
): void {
  useLayoutEffect(() => {
    const shellNode = shell.current;
    const headNode = head.current;
    // No observer, nothing to measure with: the stylesheet's default height stands.
    if (!shellNode || !headNode || typeof ResizeObserver !== 'function') return;
    // `offsetHeight` includes the head's bottom border, which `contentRect` would leave out.
    const observer = new ResizeObserver(() => {
      shellNode.style.setProperty(HEAD_HEIGHT_VAR, `${headNode.offsetHeight}px`);
    });
    observer.observe(headNode);
    return () => {
      observer.disconnect();
      shellNode.style.removeProperty(HEAD_HEIGHT_VAR);
    };
  }, [shell, head]);
}
