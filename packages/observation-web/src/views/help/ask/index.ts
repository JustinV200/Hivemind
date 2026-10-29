/**
 * The Ask's face: the panel, its page id, and the example questions.
 *
 * The Ask is the one entry of the Help screen's page list that is not a markdown page, so the
 * screen names it by `ASK_PAGE_ID` and draws `AskPanel` in place of a page's text.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/help/ask/`. Imported by `src/views/help/help_screen.tsx`.
 *
 * Key invariants:
 *   - Re-exports only; no logic lives here.
 *
 * See Also:
 *   - src/views/help/README.md for the view's files.
 */
export { AskPanel, EXAMPLE_QUESTIONS } from './ask_panel';
export { useAsk } from './use_ask';
export type { Ask, Exchange, HelpAnswer, HelpSource } from './use_ask';

// The address the Ask answers at: `#/help/ask`.
export const ASK_PAGE_ID = 'ask';
