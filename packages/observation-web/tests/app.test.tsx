/**
 * Smoke test for the Observation Hive's placeholder app component.
 *
 * This is the only test roadmap step 0.1 asks for: a proof that the front end scaffold actually
 * renders through React, jsdom and Testing Library end to end, before any real view exists.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/app.tsx through `pnpm test` (vitest run).
 *
 * Key invariants:
 *   - Asserts on rendered text content only, never on implementation detail, so it keeps passing
 *     unchanged as App grows into the real composition root in phase 12.
 *
 * See Also:
 *   - src/app.tsx for the component under test.
 */
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import App from '../src/app';

describe('App', () => {
  it('renders the Hive name', () => {
    // Render into jsdom (configured in vite.config.ts) and look up the text node by content.
    render(<App />);
    const heading = screen.getByText('HiveMind');

    // getByText already throws if nothing matches; this pins the exact text too.
    expect(heading.textContent).toBe('HiveMind');
  });
});
