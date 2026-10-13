# observation-web/src/router

The in-house hash router (ADR-0049): a pure route table and resolver (`routes.ts`) and the
`useRoute()` hook (`use_route.ts`). An unknown hash resolves to "not found", and the
loopback-only screens resolve only when the session's listener is `loopback`.
