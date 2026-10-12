/**
 * Registers the service worker when the browser has one.
 *
 * Installability needs a manifest and a registered worker. Registration waits for the page's
 * `load` event so it never competes with the first paint. A browser without service workers (and
 * the Capacitor APK, which loads from `file://`) is simply skipped: the app works without it.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/pwa/`. Called by `src/main.tsx`.
 *
 * Key invariants:
 *   - Registration failure is never fatal; the app runs without a worker.
 *   - The worker URL is relative, so the same build works behind a path prefix.
 *
 * See Also:
 *   - src/pwa/service_worker.ts for what is registered.
 */
export function registerServiceWorker(): void {
  if (!('serviceWorker' in navigator)) return;
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('./sw.js').catch(() => undefined);
  });
}
