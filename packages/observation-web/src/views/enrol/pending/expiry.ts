/**
 * The expiry choices an approval offers, and the moment each one means.
 *
 * `ApprovalBody.expires_at` is a date-time, but an operator thinks in "a week". The form offers a
 * short list; "does not expire" sends nothing (the Entrance reads an absent expiry as none), and
 * the others add whole days to the moment the form is submitted.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/enrol/pending/`. Used by `approve_form.tsx`.
 *
 * Key invariants:
 *   - Pure: the clock is passed in, never read here.
 *   - Only "does not expire" omits the member; every other choice sends a future moment.
 *
 * See Also:
 *   - docs/entrance/openapi.json, `ApprovalBody`.
 */
export interface ExpiryChoice {
  key: string;
  label: string;
  // Whole days from now; null means "send nothing".
  days: number | null;
}

export const EXPIRY_CHOICES: readonly ExpiryChoice[] = [
  { key: 'never', label: 'Does not expire', days: null },
  { key: '1', label: 'One day', days: 1 },
  { key: '7', label: 'One week', days: 7 },
  { key: '30', label: '30 days', days: 30 },
  { key: '90', label: '90 days', days: 90 },
];

const DAY_MS = 86_400_000;

export function expiryFrom(choice: ExpiryChoice, nowMs: number): string | undefined {
  return choice.days === null ? undefined : new Date(nowMs + choice.days * DAY_MS).toISOString();
}
