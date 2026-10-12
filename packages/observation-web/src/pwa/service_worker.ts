/**
 * The service worker entry: binds the caching behaviour to the real worker scope.
 *
 * Vite builds this file as a second entry to `sw.js` at the build root (see vite.config.ts), so
 * the Entrance serves it from `/sw.js` and its scope is the whole origin. All behaviour is in
 * `worker.ts`; this file only hands it `self`, which is why it is the one file in `src/pwa/` that
 * tests do not import.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/pwa/`. Registered by `register.ts` from `src/main.tsx`.
 *
 * Key invariants:
 *   - Nothing else happens at import time.
 *
 * See Also:
 *   - src/pwa/worker.ts for the behaviour.
 */
import { installCaching } from './worker';
import type { WorkerScope } from './worker';

// A worker's `self` is a ServiceWorkerGlobalScope, which the DOM lib does not describe; WorkerScope
// names exactly the members used.
installCaching(self as unknown as WorkerScope);
