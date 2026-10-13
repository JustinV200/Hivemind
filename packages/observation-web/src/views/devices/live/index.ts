/**
 * The live package's face: how a list of devices stays in step with the security stream.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/devices/live/`. Imported by `use_devices.ts` and by the
 *   Enrol view's hook (both read lists that the same stream says have changed).
 *
 * Key invariants:
 *   - Re-exports only; no logic lives here.
 *
 * See Also:
 *   - src/views/devices/live/use_burst_read.ts for the rule.
 */
export { REREAD_DELAY_MS, SECURITY_STREAM, useBurstRead } from './use_burst_read';
