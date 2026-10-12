# observation-web/src/pwa

The installable-app pieces: the service worker (`service_worker.ts`, built as the second Vite
entry to `sw.js`), its behaviour (`worker.ts`), the caching rule (`cache_rules.ts`) and the
page-side registration (`register.ts`). The worker caches the app shell only and never anything
under `/v1/`; a test keeps it that way.
