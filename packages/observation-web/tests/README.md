# observation-web/tests

Front-end tests for the Observation Hive, run with `pnpm test` (Vitest) against a jsdom
environment using React Testing Library. The tree mirrors `src/`. `support/` holds the fake
`Browser` (real WebCrypto, a scripted network, manual sockets and timers) and builders for stream
frames. Notable suites: `landing_board/client/` (signing against the document's worked example,
the reconnect policy by close code), `shell/write_routes.test.ts` (the UI can write nowhere but
the Queen's inbox and the Entrance's own routes), `pwa/` (the worker never caches `/v1/`),
`styles/tokens.test.ts` (the design's contrast numbers) and `main.test.ts` (the shared stylesheets
are imported before the App, the order the built cascade depends on).
