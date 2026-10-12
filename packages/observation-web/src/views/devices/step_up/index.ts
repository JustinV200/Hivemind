/**
 * The step-up package's face: the hook, the prompt and the standing panel.
 *
 * One shared step-up for the Devices and Enrol views. The chat has its own, written first and kept
 * apart on purpose; this one adds the standing panel the shell's banner points to.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/devices/step_up/`. Imported by the Devices and Enrol views.
 *
 * Key invariants:
 *   - Re-exports only; no logic lives here.
 *
 * See Also:
 *   - src/views/devices/step_up/use_step_up.ts for the flow.
 */
export { StepUpPanel, StepUpPrompt } from './step_up_prompt';
export { STEP_UP_CODE, useStepUp } from './use_step_up';
export type { StepUp, StepUpAsk } from './use_step_up';
