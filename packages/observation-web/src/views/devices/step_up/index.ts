/**
 * The step-up package's face: the hook, the prompt and the standing panel.
 *
 * One shared step-up for the Devices, Enrol and Chat views, with the standing panel the shell's
 * banner points to. The chat takes the hook and draws its own prompt in its dock.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/devices/step_up/`. Imported by the Devices, Enrol and
 *   Chat views.
 *
 * Key invariants:
 *   - Re-exports only; no logic lives here.
 *
 * See Also:
 *   - src/views/devices/step_up/use_step_up.ts for the flow.
 */
export { StepUpPanel, StepUpPrompt } from './step_up_prompt';
export { needsStepUp, STEP_UP_CODE, useStepUp } from './use_step_up';
export type { StepUp, StepUpAsk } from './use_step_up';
