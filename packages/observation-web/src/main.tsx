/**
 * Browser entry point: mounts the Observation Hive app into the DOM.
 *
 * The Observation Hive is the Hive's live read-only dashboard (see src/app.tsx). This module is
 * the one place that talks to the real DOM directly, apart from the service worker registration
 * it triggers, so a future change to how the app is mounted touches only this file.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web). Loaded by index.html's script tag. Imports the shared stylesheets
 *   (tokens first, as the others read them) before the App, chooses the platform's `Browser` (the
 *   web binding, or the Android shell's inside the APK, ADR-0050), renders App from src/app.tsx
 *   and registers the PWA worker on the web only.
 *
 * Key invariants:
 *   - The shared stylesheets are imported before './app'. Vite concatenates CSS in import order,
 *     and importing the App pulls in every view's own stylesheet, so this order is what puts a
 *     view's rules after the shared ones in the built bundle and lets them win ties of equal
 *     specificity (the Fleet's quiet place tag over `.badge`, its re-sort button over `.button`).
 *     tests/main.test.ts guards it.
 *   - Throws immediately if the #root element is missing, rather than silently rendering nothing,
 *     so a broken index.html fails loudly in development.
 *
 * See Also:
 *   - index.html for the #root element this mounts into.
 *   - src/app.tsx for the component rendered here.
 */
import { Capacitor } from '@capacitor/core';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
// The shared sheets come first, before any import that carries a view's own CSS (see the header).
import './styles/tokens.css';
import './styles/base.css';
import './styles/shell.css';
import './styles/head.css';
import './styles/components.css';
import './styles/thoughts.css';
import App from './app';
import { chooseBrowser, realBrowser } from './landing_board/client';
import { registerServiceWorker } from './pwa/register';
import { applyStoredTheme } from './shell/theme';

// The viewer's theme goes on <html> before anything is drawn, so the first paint is already it.
applyStoredTheme();

// #root is declared in index.html; its absence means the HTML shell itself is broken.
const rootElement = document.getElementById('root');
if (!rootElement) {
  throw new Error('Observation Hive: #root element not found in index.html');
}

// ADR-0050: the platform is chosen once, here. The Android half is loaded only inside the APK, so
// the web bundle never carries it.
const isNative = Capacitor.isNativePlatform();

void chooseBrowser({
  isNative: () => isNative,
  web: () => realBrowser(),
  android: async () => (await import('./landing_board/client/platform/android')).androidBrowser(),
}).then((browser) => {
  createRoot(rootElement).render(
    <StrictMode>
      <App browser={browser} />
    </StrictMode>,
  );
});

// An APK carries its own bundle, so a service worker caching it would only serve stale code.
if (!isNative) registerServiceWorker();
