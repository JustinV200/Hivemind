/**
 * Smoke test for the Observation Hive app: it mounts and asks for a login first.
 *
 * Roadmap step 0.1 asked for a proof that the scaffold renders through React, jsdom and Testing
 * Library. With the shell in place the first thing an unauthenticated visitor sees is the login
 * screen, so this test pins that. The shell, strip, nav and routes are covered in tests/shell/.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/app.tsx through `pnpm test` (vitest run).
 *
 * Key invariants:
 *   - Uses the fake browser, so the app makes no real network call.
 *   - Asserts on rendered text only, never on implementation detail.
 *
 * See Also:
 *   - src/app.tsx for the component under test.
 *   - tests/shell/shell.test.tsx for the rest of the app's behaviour.
 */
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import App from '../src/app';
import { createFakeBrowser } from './support/fake_browser';

describe('App', () => {
  it('asks a visitor with no session to log in', async () => {
    // Render into jsdom (configured in vite.config.ts); with no stored session the gate opens login.
    render(<App browser={createFakeBrowser().browser} />);
    const heading = await screen.findByRole('heading', { name: 'Log in to the Observation Hive' });

    // findByRole already throws if nothing matches; this pins the exact text too.
    expect(heading.textContent).toBe('Log in to the Observation Hive');
  });
});
