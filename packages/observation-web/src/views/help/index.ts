/**
 * The Help view's face: the route component, the tour the shell mounts, and the pages.
 *
 * The view is a package (the pages and their markdown, a search, a stylesheet and the tour), so
 * importers take it from here and the layout inside can change without touching the shell. The
 * pages are exported for the glossary test, which proves every bee word the UI shows has an entry.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/help/`. Imported by `src/shell/outlet.tsx` (the screen)
 *   and `src/shell/shell.tsx` (the tour).
 *
 * Key invariants:
 *   - Re-exports only; no logic lives here.
 *
 * See Also:
 *   - src/views/help/README.md for the view's files.
 */
export { HelpScreen } from './help_screen';
export { HELP_PAGES, helpPage } from './pages';
export type { HelpPage } from './pages';
export { Tour, TOUR_STEPS } from './tour';
export type { TourStep } from './tour';
