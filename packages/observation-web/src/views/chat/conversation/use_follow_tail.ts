/**
 * `useFollowTail(tail, seeded)`: keep the newest line in view, never while the reader reads upward.
 *
 * The conversation is oldest first with the composer docked below it, so a page that opened at the
 * top would show the oldest line and hide the newest under the dock. This hook scrolls the page to
 * its end once the seed has landed, and again whenever a new item lands (a line, the inbox's
 * forms under it) while the reader was already at the end; a reader who has scrolled up to read
 * is left where they are, and the new line waits below. "At the end" is remembered from the
 * last scroll event, so it describes the page before the new line was drawn, not after.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/chat/conversation/`. Called by `conversation.tsx` with
 *   the key of the last item it draws; touches the window's scroll position and nothing else.
 *
 * Key invariants:
 *   - Scrolls at most once per change of `tail`, and never before the seed has landed.
 *   - Reads no slice and makes no request.
 *
 * See Also:
 *   - docs/observation/design.md section 5.6.
 */
import { useEffect, useLayoutEffect, useRef } from 'react';

const END_SLACK_PX = 64; // Within a line of the end still counts as reading the end.

// Whether the window shows the end of the page right now.
function atEnd(): boolean {
  const root = document.documentElement;
  return window.innerHeight + window.scrollY >= root.scrollHeight - END_SLACK_PX;
}

// The page's end, where the dock sits in its own place and the newest line is just above it.
function scrollToEnd(): void {
  window.scrollTo({ top: document.documentElement.scrollHeight });
}

export function useFollowTail(tail: string, seeded: boolean): void {
  // Where the reader was before the latest change, from the last scroll; at the end to begin with.
  const wasAtEnd = useRef(true);
  useEffect(() => {
    const remember = (): void => {
      wasAtEnd.current = atEnd();
    };
    window.addEventListener('scroll', remember, { passive: true });
    return () => window.removeEventListener('scroll', remember);
  }, []);

  const shown = useRef<string | null>(null);
  const seededBefore = useRef(false);
  // A layout effect, so the scroll lands before the new line is painted and nothing flashes.
  useLayoutEffect(() => {
    const first = seeded && !seededBefore.current;
    const changed = tail !== shown.current;
    seededBefore.current = seeded;
    shown.current = tail;
    if (!seeded || (!first && !changed)) return;
    // The seed always lands at the end; a later line only when the reader was already there.
    if (first || wasAtEnd.current) scrollToEnd();
  }, [tail, seeded]);
}
