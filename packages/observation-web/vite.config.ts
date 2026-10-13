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
//   - The worker's file name is fixed (`sw.js`, no hash); every other file is content-hashed.
//
// See Also:
//   - package.json's `dev`, `build` and `test` scripts, which invoke this config.
//   - src/pwa/ for the worker and docs/adr/0049-observation-hive-dashboard-stack.md.
/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  base: './',
  plugins: [react()],
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
