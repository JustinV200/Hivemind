/**
 * Vitest setup: unmount rendered components after every test.
 *
 * Testing Library only cleans up on its own when the test globals (`afterEach`) exist, and this
 * project imports its test functions explicitly. Without this file a component rendered by one
 * test would still be in the document during the next, and queries would find two of everything.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Loaded by `test.setupFiles` in vite.config.ts.
 *
 * Key invariants:
 *   - Runs after every test in every file, in the jsdom environment.
 *   - `window.scrollTo` is a no-op, so a view that follows its newest line can mount here.
 *
 * See Also:
 *   - vite.config.ts for where it is registered.
 */
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

afterEach(() => cleanup());

// jsdom has no layout, so a scroll is a no-op here rather than a "not implemented" error.
window.scrollTo = () => undefined;
