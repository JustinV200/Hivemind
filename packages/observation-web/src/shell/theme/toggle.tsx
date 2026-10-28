/**
 * `ThemeToggle`: the sun and moon button in the top bar that switches light and dark.
 *
 * The button shows the theme the page is in, as an icon and the word (`Light` or `Dark`), and
 * pressing it switches to the other; its accessible name says both, so a screen reader knows what
 * it shows and what a press does. The word is hidden under 720 px to keep the bar at 48 px, and
 * the name carries it there.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/shell/theme/`. Rendered by `head/top_bar.tsx`; the choice
 *   lives in `choice.ts` and on `<html>`.
 *
 * Key invariants:
 *   - Never colour alone: the icon differs by shape (a sun, a crescent) and the word is there.
 *   - A press stores the choice, so the next load keeps it.
 *
 * See Also:
 *   - src/shell/theme/choice.ts and src/styles/tokens.css.
 */
import type { ReactElement } from 'react';
import { useTheme } from './choice';

const SUN =
  'M12 4V2M12 22v-2M4 12H2M22 12h-2M5.6 5.6 4.2 4.2M19.8 19.8l-1.4-1.4M5.6 18.4l-1.4 1.4M19.8 4.2l-1.4 1.4M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10z';
const MOON = 'M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z';

export function ThemeToggle(): ReactElement {
  const [theme, setTheme] = useTheme();
  const next = theme === 'dark' ? 'light' : 'dark';
  const word = theme === 'dark' ? 'Dark' : 'Light';
  return (
    <button
      type="button"
      className="button topbar-theme"
      aria-label={`Theme: ${word}. Switch to ${next}`}
      title={`Switch to ${next}`}
      onClick={() => setTheme(next)}
    >
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <path d={theme === 'dark' ? MOON : SUN} />
      </svg>
      <span className="topbar-theme-word">{word}</span>
    </button>
  );
}
