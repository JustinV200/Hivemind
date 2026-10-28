/**
 * Guard for the stylesheet order in src/main.tsx: the shared sheets come before the App.
 *
 * Vite concatenates CSS in import order, and importing the App pulls in every view's own
 * stylesheet, so the six shared sheets must be imported before './app' for a view's rule to beat
 * a shared rule of equal specificity. Round 6 of the design review found them after it: in the
 * built bundle `.badge` beat the Fleet's quiet place tag, which drew the badge's 2px border, and
 * `.button` beat its re-sort button, which lost its accent. No lint rule orders imports, so this
 * reads the file and checks.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Reads src/main.tsx as text through the file reader in
 *   `tests/support/cascade.ts`.
 *
 * Key invariants:
 *   - A sheet is a top-level `import './styles/<name>.css';` line; anything else is not counted.
 *
 * See Also:
 *   - src/main.tsx, the entry point under test.
 *   - docs/observation/design.md section 4.1 for the tag the wrong order broke.
 */
import { expect, it } from 'vitest';
import { readCss } from './support/cascade';

it('imports the six shared stylesheets before the App', async () => {
  const lines = (await readCss('src/main.tsx')).split('\n');
  const sheets = lines.flatMap((line, i) =>
    /^import '\.\/styles\/[\w-]+\.css';$/.test(line) ? [i] : [],
  );
  const app = lines.findIndex((line) => line.startsWith("import App from './app'"));
  expect(sheets).toHaveLength(6);
  expect(app).toBeGreaterThan(Math.max(...sheets));
});
