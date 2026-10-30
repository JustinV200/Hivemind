// Vite build and test configuration for the Observation Hive front end.
//
// This wires up the React plugin for JSX/Fast Refresh and, in the same file, the Vitest test
// runner's environment. Keeping both here (rather than a separate vitest.config.ts) avoids two
// configs drifting apart, per codingrules 5.2 (one concept per file, applied loosely to config).
// The build has two entries: the page (index.html) and the service worker, which must land at
// the build root as `sw.js` so its scope is the whole origin.
//
// Fits into the Hive:
//   Layer 7 (observation-web). Read by `vite`, `vite build` and `vitest run`, the three scripts
//   that exercise this package's build and test pipeline. Nothing in the Hive imports this file.
//
// Key invariants:
//   - The jsdom test environment is required because app.tsx renders real DOM nodes tests assert
//     against; without it `document` is undefined under Node's default test environment.
//   - `base: './'` keeps asset URLs relative, so the same build runs from the Entrance's root,
//     behind a path prefix and from Capacitor's `file://` origin (ADR-0049).
//   - The worker's file name is fixed (`sw.js`, no hash); every other file is content-hashed,
//     except the help pages, copied unhashed under `help/` for the Entrance to read by name.
//
// See Also:
//   - package.json's `dev`, `build` and `test` scripts, which invoke this config.
//   - src/pwa/ for the worker and docs/adr/0049-observation-hive-dashboard-stack.md.
/// <reference types="vitest/config" />
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';
import type { Plugin } from 'vite';
import react from '@vitejs/plugin-react';

// The help pages the Help screen bundles, which the build also copies beside the app.
const HELP_CONTENT = fileURLToPath(new URL('./src/views/help/content', import.meta.url));

// Copies every help page into the build as `help/<page>.md`, so the Entrance's Ask answers from
// exactly the words the Help screen shows, from one source (ADR-0070). Build only: the dev
// server and the tests read the pages through the `?raw` imports.
function helpPages(): Plugin {
  return {
    name: 'hive-help-pages',
    apply: 'build',
    generateBundle() {
      for (const name of readdirSync(HELP_CONTENT).filter((file) => file.endsWith('.md'))) {
        this.emitFile({
          type: 'asset',
          fileName: `help/${name}`,
          source: readFileSync(join(HELP_CONTENT, name)),
        });
      }
    },
  };
}

export default defineConfig({
  base: './',
  plugins: [react(), helpPages()],
  build: {
    rollupOptions: {
      input: { index: 'index.html', sw: 'src/pwa/service_worker.ts' },
      output: {
        // Only the worker keeps a stable name; the page's chunks are hashed for cache busting.
        entryFileNames: (chunk) => (chunk.name === 'sw' ? 'sw.js' : 'assets/[name]-[hash].js'),
      },
    },
  },
  test: {
    // jsdom simulates a browser DOM so @testing-library/react can render app.tsx in tests.
    environment: 'jsdom',
    // Unmounts rendered components between tests (see tests/setup.ts).
    setupFiles: ['tests/setup.ts'],
    // Vitest blanks stylesheets by default; the token test reads the real text of ours.
    css: { include: [/src\/styles\/.*\.css/] },
  },
});
