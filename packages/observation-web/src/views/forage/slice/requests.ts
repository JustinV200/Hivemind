/**
 * Fold the Forage trail events into the list of `ForageRequest`s and the Queen's answers.
 *
 * A Warden that wants shared Forage beyond its standing grant sends a `ForageRequest`; the Queen
 * records `forage.requested` when it lands and later `forage.granted` or `forage.denied` on the
 * same grant (a denial carries a reason code). No read route lists open requests, so the view
 * builds the list from the stream: a request is pending from its `forage.requested` event until an
 * answer names the same grant. An answer with nothing pending under it (a plain grant issue, a
 * denial of a task) is not a request's answer and changes nothing.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/forage/slice/`. Called by `reduce.ts` for every frame.
 *
 * Key invariants:
 *   - Pure and bounded: the list is newest first and holds at most `MAX_REQUESTS` records.
 *   - A request is answered once; the newest pending request on the grant takes the answer.
 *   - Payload values are read as text only when they are text; nothing else is trusted.
 *
 * See Also:
 *   - hivemind/queen/ticks/forage.py for the events and their payloads.
 */
import { MAX_REQUESTS } from './state';
import type { Decision, RequestRecord, TrailEventView } from './state';

// A payload value as text, or null when the key is absent or holds something else.
export function payloadText(event: TrailEventView, key: string): string | null {
  const value = event.payload[key];
  return typeof value === 'string' ? value : null;
}

const ANSWERS: Readonly<Record<string, Decision>> = {
  'forage.granted': 'granted',
  'forage.denied': 'denied',
};

// The newest pending request on the grant, and for an answer that names a kind, of that kind.
function openRequest(requests: readonly RequestRecord[], event: TrailEventView): number {
  const kind = payloadText(event, 'request_kind');
  return requests.findIndex(
    (r) =>
      r.decision === 'pending' &&
      r.grantId === event.subject_id &&
      (kind === null || r.requestKind === null || r.requestKind === kind),
  );
}

// Records a new ask at the head, unless this very event was already folded (a replayed frame).
function asked(
  requests: readonly RequestRecord[],
  event: TrailEventView,
): readonly RequestRecord[] {
  if (requests.some((r) => r.id === event.id)) return requests;
  const record: RequestRecord = {
    id: event.id,
    grantId: event.subject_id,
    requestKind: payloadText(event, 'request_kind'),
    requestedAt: event.at,
    decision: 'pending',
    decidedAt: null,
    reason: null,
  };
  return [record, ...requests].slice(0, MAX_REQUESTS);
}

// Closes the open request the event answers; returns the same list when none is open.
function answered(
  requests: readonly RequestRecord[],
  event: TrailEventView,
  decision: Decision,
): readonly RequestRecord[] {
  const at = openRequest(requests, event);
  if (at < 0) return requests;
  const reason = decision === 'denied' ? payloadText(event, 'reason') : null;
  return requests.map((r, i) => (i === at ? { ...r, decision, decidedAt: event.at, reason } : r));
}

export function foldRequests(
  requests: readonly RequestRecord[],
  event: TrailEventView,
): readonly RequestRecord[] {
  if (event.kind === 'forage.requested') return asked(requests, event);
  const decision = ANSWERS[event.kind];
  return decision ? answered(requests, event, decision) : requests;
}
