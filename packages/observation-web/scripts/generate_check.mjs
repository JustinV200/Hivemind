// Fail when src/landing_board/types.ts no longer matches docs/entrance/openapi.json.
//
// The generated types are the Landing Board contract as the front end sees it (ADR-0046), so
// CI must catch a document change that was committed without regenerating them. This script
// runs the same generator as `pnpm generate` into a temporary file and compares bytes, which
// leaves the committed file untouched and works on every host (no shell diff needed).
//
// Fits into the Hive:
//   Layer 7 (observation-web tooling). Run by `pnpm generate:check` and by the CI front-end job.
//
// Key invariants:
//   - Exits 0 when the files are identical, 1 when they differ (with the fix named), and
//     propagates the generator's own failure.
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
// The package's export map hides its CLI, so find it beside the package.json it does expose.
const generator = join(
  dirname(require.resolve('openapi-typescript/package.json')),
  'bin',
  'cli.js',
);
const document = fileURLToPath(new URL('../../../docs/entrance/openapi.json', import.meta.url));
const committed = fileURLToPath(new URL('../src/landing_board/types.ts', import.meta.url));

// A throwaway directory keeps the regenerated copy out of the source tree.
const scratch = mkdtempSync(join(tmpdir(), 'hive-types-'));
try {
  const regenerated = join(scratch, 'types.ts');
  execFileSync(process.execPath, [generator, document, '-o', regenerated], { stdio: 'ignore' });
  const same = readFileSync(regenerated, 'utf8') === readFileSync(committed, 'utf8');
  if (!same) {
    console.error('src/landing_board/types.ts is out of date with docs/entrance/openapi.json.');
    console.error('Run `pnpm generate` and commit the result.');
    process.exitCode = 1;
  }
} finally {
  rmSync(scratch, { recursive: true, force: true });
}
