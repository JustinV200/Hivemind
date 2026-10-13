/**
 * Type declaration for importing a stylesheet's text with Vite's `?raw` suffix.
 *
 * The token test reads the CSS files as strings; Vite supplies the text at run time, and this
 * declaration tells TypeScript the import is a string.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Used by tests/styles/tokens.test.ts.
 *
 * Key invariants:
 *   - Declares only `.css?raw` modules; nothing else is widened.
 *
 * See Also:
 *   - tests/styles/tokens.test.ts.
 */
declare module '*.css?raw' {
  const css: string;
  export default css;
}
