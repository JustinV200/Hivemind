/**
 * The Devices view's face: the route component, the slice `src/app.tsx` registers and the step-up.
 *
 * The view is a package (a slice, a list, an Entrance panel, a shared step-up, a hook and a
 * stylesheet), so importers take it from here. The step-up is exported because the Enrol view
 * asks for it too.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/devices/`. Imported by `src/shell/outlet.tsx` (the
 *   screen), `src/app.tsx` (the slice) and `src/views/enrol/` (the step-up and the live read).
 *
 * Key invariants:
 *   - Re-exports only; no logic lives here.
 *
 * See Also:
 *   - src/views/devices/README.md for the view's files.
 */
export { DevicesScreen } from './devices_screen';
export { devicesSlice } from './slice';
export type { DevicesEvent, DevicesState } from './slice';
export { StepUpPanel, StepUpPrompt, useStepUp } from './step_up';
export type { StepUp } from './step_up';
export { SECURITY_STREAM, useBurstRead } from './live';
export { parseCapabilities } from './list/format';
