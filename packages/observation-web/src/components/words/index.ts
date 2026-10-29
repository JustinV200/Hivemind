/**
 * The words package's face: the glosses and the enum tables the views share.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/components/words/`. Re-exported by `src/components/index.ts`.
 *
 * Key invariants:
 *   - Re-exports only; no logic lives here.
 *
 * See Also:
 *   - glosses.ts for the view sentences, enums.ts for the code tables.
 */
export { VIEW_HINTS } from './glosses';
export type { GlossedView } from './glosses';
export {
  alarmKindWords,
  CAPABILITY_WORDS,
  capabilityWords,
  CLEARANCE_MEANINGS,
  CLEARANCE_NAMES,
  clearanceWords,
  inboxKindWords,
  KNOWN_CAPABILITIES,
  plainCode,
  riskTierWords,
} from './enums';
export type { HoneyClearance } from './enums';
