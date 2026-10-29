/**
 * Small text formatter for the Fleet's cells: a lease's age in seconds as a short duration.
 *
 * The aggregate gives a lease age in seconds; the table shows it the way a person reads one
 * (`1h 05m`). A schema word (`GUARD_BEE`, `READ_ONLY`) goes through the shared `plainCode` of
 * `components/words`, not a formatter of its own. Pure, so the rule is testable without a page.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/fleet/table/`. Used by `fleet_row.tsx`.
 *
 * Key invariants:
 *   - No clock: an age is shown as of the read that carried it, never ticked locally.
 *   - A negative or missing age shows as `none`, never as NaN.
 *
 * See Also:
 *   - docs/observation/design.md section 4.3.
 */
export function leaseAge(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return 'none';
  const whole = Math.floor(seconds);
  if (whole < 60) return `${whole}s`;
  const minutes = Math.floor(whole / 60);
  if (minutes < 60) return `${minutes}m ${whole % 60}s`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h ${minutes % 60}m`;
  return `${Math.floor(hours / 24)}d ${hours % 24}h`;
}
