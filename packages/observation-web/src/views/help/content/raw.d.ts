/**
 * Type declaration for importing a help page's markdown text with Vite's `?raw` suffix.
 *
 * `pages.ts` reads each page as a string; Vite supplies the text at run time, and this declaration
 * tells TypeScript the import is a string.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/help/content/`. Used by `src/views/help/pages.ts`.
 *
 * Key invariants:
 *   - Declares only `.md?raw` modules; nothing else is widened.
 *
 * See Also:
 *   - tests/support/css_raw.d.ts, the same declaration for stylesheets.
 */
declare module '*.md?raw' {
  const text: string;
  export default text;
}
