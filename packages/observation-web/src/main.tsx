/**
 * Browser entry point: mounts the Observation Hive app into the DOM.
 *
 * The Observation Hive is the Hive's live read-only dashboard (see src/app.tsx). This module is
 * the one place that talks to the real DOM directly — everything else renders through React — so
 * that a future change to how the app is mounted (a router, a PWA service worker) touches only
 * this file.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web). Loaded by index.html's script tag. Renders App from src/app.tsx.
 *
 * Key invariants:
 *   - Throws immediately if the #root element is missing, rather than silently rendering nothing,
 *     so a broken index.html fails loudly in development.
 *
 * See Also:
 *   - index.html for the #root element this mounts into.
 *   - src/app.tsx for the component rendered here.
 */
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './app';

// #root is declared in index.html; its absence means the HTML shell itself is broken.
const rootElement = document.getElementById('root');
if (!rootElement) {
  throw new Error('Observation Hive: #root element not found in index.html');
}

createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
