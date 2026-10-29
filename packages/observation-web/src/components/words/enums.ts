/**
 * Plain words for the codes the Landing Board sends: Alarm kinds, inbox kinds, clearances,
 * risk tiers, the Capping gate's proposal states, the judge's findings, device capabilities and
 * a Cell's operating-system family.
 *
 * The schema speaks in shouting codes (`CONTEXT_PRESSURE`, `SCRATCH_WRITE`, `entrance:submit`)
 * and the screen speaks to a person, so each enum has one table here that turns a code into a
 * short phrase. A code the table does not know is shown as sent, with its underscores read as
 * spaces and in lower case, never hidden: the Entrance may add a member before this file learns
 * it. More than one view reads each table, which is why they live here and not in a view.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/components/words/`. Read by the Cell page, the Chat, the
 *   Thoughts and Attendant inboxes, the Honey browser, the Capping view and the Devices views.
 *
 * Key invariants:
 *   - Pure: no state, no I/O; every function is total over strings.
 *   - The README's words are kept where the code is one (`Alarm`, `Cell Wax`); a phrase explains,
 *     it never renames.
 *
 * See Also:
 *   - README.md for the vocabulary; docs/observation/design.md section 8.
 */
import type { components } from '../../landing_board/types';

type Schemas = components['schemas'];
export type HoneyClearance = Schemas['HoneyClearance'];

// A code as a person would say it: lower case, spaces for underscores.
export const plainCode = (code: string): string => code.replace(/_/g, ' ').toLowerCase();

// What went wrong, as the escalation policy names it (hivemind.supervision.alarm.AlarmKind).
const ALARM_KINDS: Readonly<Record<string, string>> = {
  WORKER_FAILED: 'a Worker failed its task',
  WORKER_CRASHED: 'a Worker crashed',
  WORKER_STALLED: 'a Worker stalled',
  ACCEPTANCE_FAILED: 'the result failed its acceptance check',
  POSTCONDITION_FAILED: 'a check after the work failed',
  CONTEXT_OVERFLOW: 'context overflowed',
  CONTEXT_PRESSURE: 'context nearly full',
  GRANT_EXCEEDED: 'spent past its grant',
  PROVIDER_UNAVAILABLE: 'a model provider cannot be reached',
  AUDIT_FAILED: 'an audit failed',
  CELL_UNREACHABLE: 'the Cell cannot be reached',
  QUOTA_EXCEEDED: 'its scratch folder is over quota',
  EXOSKELETON_FAILED: 'the Exoskeleton could not attach',
  SECURITY: 'a security event',
  // The two the shipped policy sends all the way up to the human, so they must read at a glance.
  PATROL_ANOMALY: 'a Patrol found something worth your eyes',
  NURSE_UNRESOLVED: 'the Nurse Bee still wanted changes after the last fix round',
  OTHER: 'something else went wrong',
};

export const alarmKindWords = (kind: string): string => ALARM_KINDS[kind] ?? plainCode(kind);

// Where the gate has a proposal (`ProposalState`), and its verdict (`CAPPED` or `REJECTED`),
// in the words the Capping view's rates cards count by: `CAPPED` is the README's word for
// approved, which a newcomer cannot guess from the code.
const PROPOSAL_STATES: Readonly<Record<string, string>> = {
  PROPOSED: 'Proposed',
  CHECKING: 'Checking',
  CAPPED: 'Approved',
  APPLIED: 'Applied',
  VERIFIED: 'Verified',
  REJECTED: 'Refused',
  ROLLED_BACK: 'Rolled back',
};

export const proposalStateWords = (state: string): string =>
  PROPOSAL_STATES[state] ?? plainCode(state);

// The judge's finding on a sampled audit (`JudgeOutcome`), in the register of the gate's states
// above, so `REJECT` and `REJECTED` read as one word on the Capping page.
const JUDGE_OUTCOMES: Readonly<Record<string, string>> = {
  APPROVE: 'Approved',
  REQUEST_CHANGES: 'Wants changes',
  REJECT: 'Refused',
};

export const judgeOutcomeWords = (outcome: string): string =>
  JUDGE_OUTCOMES[outcome] ?? plainCode(outcome);

// What kind of thing landed in a supervisor's inbox (`InboxKind`).
const INBOX_KINDS: Readonly<Record<string, string>> = {
  ALARM: 'Alarm',
  QUESTION: 'question',
  HUMAN_MESSAGE: 'message from you',
  WAGGLE_MESSAGE: 'message from a bee',
  TIMER: 'timer',
  HEARTBEAT: 'heartbeat',
  WATCH_OBSERVATION: 'watch observation',
  GUARD_REQUEST: 'Guard Bee request',
};

export const inboxKindWords = (kind: string): string => INBOX_KINDS[kind] ?? plainCode(kind);

// The README's clearance names and what each one means in a word.
export const CLEARANCE_NAMES: Readonly<Record<HoneyClearance, string>> = {
  C0: 'Wildflower',
  C1: 'Apiary',
  C2: 'Royal',
};

export const CLEARANCE_MEANINGS: Readonly<Record<HoneyClearance, string>> = {
  C0: 'public: nothing sensitive',
  C1: 'internal: not personal',
  C2: 'personal or sensitive',
};

// `C1 Apiary`, the form every badge and tag shows a clearance in.
export const clearanceWords = (label: HoneyClearance): string =>
  `${label} ${CLEARANCE_NAMES[label]}`;

// The gate's risk tiers (`RiskTier`), least to most dangerous, as what the action would do.
const RISK_TIERS: Readonly<Record<string, string>> = {
  READ_ONLY: 'only reads',
  SCRATCH_WRITE: 'writes inside its scratch folder',
  OUTSIDE_SCRATCH_WRITE: 'writes outside its scratch folder',
  NETWORK_EGRESS: 'talks to the network',
  SPEND: 'spends money',
  DEVICE_COMMAND: 'commands a device',
  IRREVERSIBLE: 'cannot be undone',
};

export const riskTierWords = (tier: string): string => RISK_TIERS[tier] ?? plainCode(tier);

// A Cell's operating-system family (`OsFamily`), spelled as its maker spells it: `plainCode`
// would give `macos`, which no reader writes.
const OS_FAMILIES: Readonly<Record<string, string>> = {
  LINUX: 'Linux',
  WINDOWS: 'Windows',
  MACOS: 'macOS',
};

export const osWords = (os: string): string => OS_FAMILIES[os] ?? plainCode(os);

// What a device capability lets it do; the set the Hive Stand console holds names them all.
export const CAPABILITY_WORDS: Readonly<Record<string, string>> = {
  observe: 'may watch the Hive',
  'observe:thoughts': "may read bees' thoughts",
  'observe:honey:*': 'may read every Honey folder',
  'entrance:submit': 'may send goals and messages',
  'entrance:answer': 'may answer questions and acknowledge Alarms',
  'entrance:push': 'may receive push notices',
  'entrance:steward': 'may manage devices and the Entrance',
  'honey:clearance:c2': 'may read personal (C2) items',
  'cell:hive_stand': 'may work on the Hive Stand itself',
  'cell:virtual': 'may work on Virtual Cells',
  'cell:comb_shield:*': 'may use every shield level',
  'spend:*': 'may spend without a cap',
};

// The capabilities a form offers as choices, in the order the console lists them.
export const KNOWN_CAPABILITIES: readonly string[] = Object.keys(CAPABILITY_WORDS);

// `may send goals and messages (entrance:submit)`: the phrase first, the code kept for the operator.
export function capabilityWords(capability: string): string {
  const words = CAPABILITY_WORDS[capability];
  return words ? `${words} (${capability})` : capability;
}
