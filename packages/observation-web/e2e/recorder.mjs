/**
 * What the page did while a step ran: its requests, its errors, and whether it was ever reloaded.
 *
 * The exit run must prove two negatives that no screenshot shows: the Observation Hive never wrote
 * anywhere but the chat and the Entrance's own routes, and nothing refreshed the page to make a
 * view move. So every request the page (and its service worker) makes is logged as method plus
 * path, every uncaught page error is kept, and the number of full document loads is counted: a
 * hash route change or a stream frame never fires `load`, a reload always does. A marker planted
 * before the page's own scripts run survives exactly as long as the document does, which is a
 * second, independent check on the same fact.
 *
 * Fits into the Hive:
 *   Test tooling for phase 12's exit run, not shipped. Used by `observation_run.mjs`.
 *
 * Key invariants:
 *   - Only path and method are kept, never a query string, header or body, so a log cannot hold
 *     a session token or a typed password.
 *   - The recorder only observes; it never changes a request.
 */

const MARKER = '__hive_e2e_marker';

export function record(page, context) {
  const state = { requests: [], errors: [], consoleErrors: [], loads: 0, sockets: [] };
  page.on('request', (request) => {
    const url = new URL(request.url());
    state.requests.push({ method: request.method(), path: url.pathname });
  });
  // A socket's whole life is kept (opened, then closed), since a stream that drops and reopens is
  // exactly what a "live" claim must be checked against.
  page.on('websocket', (socket) => {
    const path = new URL(socket.url()).pathname;
    state.sockets.push(`open ${path}`);
    socket.on('close', () => state.sockets.push(`close ${path}`));
  });
  page.on('pageerror', (error) => state.errors.push(String(error.message ?? error)));
  page.on('console', (message) => {
    if (message.type() === 'error') state.consoleErrors.push(message.text());
  });
  page.on('load', () => (state.loads += 1));
  // A fresh random marker per document: if it changes, the page was loaded again.
  const planted = page.addInitScript((name) => {
    window[name] = Math.random().toString(36).slice(2);
  }, MARKER);
  context.on('serviceworker', (worker) => {
    worker.on('request', (request) => {
      const url = new URL(request.url());
      state.requests.push({ method: request.method(), path: url.pathname, worker: true });
    });
  });
  return { state, planted };
}

export async function marker(page) {
  return page.evaluate((name) => window[name] ?? null, MARKER);
}

// Everything logged since the last drain, and the log emptied for the next step.
export function drain(state) {
  const taken = {
    requests: state.requests.splice(0),
    sockets: state.sockets.splice(0),
  };
  return taken;
}

// Counts of requests by method, for a readable report.
export function summarise(requests) {
  const counts = {};
  for (const { method, path } of requests) {
    if (!path.startsWith('/v1/')) continue;
    const key = `${method} ${path}`;
    counts[key] = (counts[key] ?? 0) + 1;
  }
  return counts;
}
