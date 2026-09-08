// Vite build and test configuration for the Observation Hive front end.
//
// This wires up the React plugin for JSX/Fast Refresh and, in the same file, the Vitest test
// runner's environment. Keeping both here (rather than a separate vitest.config.ts) avoids two
// configs drifting apart, per codingrules 5.2 (one concept per file, applied loosely to config).
//
// Fits into the Hive:
//   Layer 7 (observation-web). Read by `vite`, `vite build` and `vitest run`, the three scripts
//   that exercise this package's build and test pipeline. Nothing in the Hive imports this file.
//
// Key invariants:
//   - The jsdom test environment is required because app.tsx renders real DOM nodes tests assert
//     against; without it `document` is undefined under Node's default test environment.
//
// See Also:
//   - package.json's `dev`, `build` and `test` scripts, which invoke this config.
/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  test: {
    // jsdom simulates a browser DOM so @testing-library/react can render app.tsx in tests.
    environment: 'jsdom',
  },
});
