/**
 * Test builders for the stream frames the shell folds, with every required field filled in.
 *
 * The generated frame types have many required members that a test about counting Cells does not
 * care about. These builders fill them with plain defaults and let a test override only what it
 * is about, so each test reads as its point and still type-checks against the generated types.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Used by the store, feed, shell and app tests.
 *
 * Key invariants:
 *   - Return types are the generated schema types, so a contract change breaks these builders
 *     (and so every test) at compile time.
 *
 * See Also:
 *   - src/landing_board/types.ts, generated.
 */
import type { components } from '../../src/landing_board/types';

type Schemas = components['schemas'];

export function cellFrame(cell: Partial<Schemas['CellView']> = {}): Schemas['CellFrame'] {
  return {
    type: 'cell',
    cell: {
      access_level: 'SCRATCH',
      can_host_model: false,
      comb_shield: 'MEADOW',
      current_tasks: [],
      has_browser: false,
      has_display: false,
      id: 'cell_1',
      kind: 'VIRTUAL',
      last_event: null,
      last_event_at: null,
      lease_state: null,
      mode: 'ACTIVE',
      name: 'cell',
      os: 'LINUX',
      source: 'local',
      virtual_status: 'READY',
      warden_id: null,
      ...cell,
    },
  };
}

export function telemetryFrame(
  sample: Partial<Schemas['TelemetrySampleView']> = {},
): Schemas['TelemetryFrame'] {
  return {
    type: 'telemetry',
    sample: {
      at: '2026-01-01T00:00:00Z',
      blockers: [],
      context_window: 128000,
      goal: 'goal',
      last_actions: [],
      spend: 0,
      task_id: null,
      tokens_used: 0,
      warden_id: 'warden_1',
      warden_state: 'ACTIVE',
      worker_id: null,
      worker_state: null,
      ...sample,
    },
  };
}

export function episodeFrame(
  episode: Partial<Schemas['EpisodeView']> = {},
): Schemas['EpisodeFrame'] {
  return {
    type: 'episode',
    episode: {
      action: 'act',
      at: '2026-01-01T00:00:00Z',
      clearance: 'C1',
      cost_usd: null,
      decision: 'decide',
      id: 'ep_1',
      input_tokens: null,
      is_autopilot: false,
      output_tokens: null,
      principal: 'queen',
      prompt_ref: null,
      reasoning_summary: null,
      slot: 'QUEEN',
      trigger_kind: 'tick',
      trigger_ref: null,
      trigger_summary: 'tick',
      ...episode,
    },
  };
}

// The seed as /v1/summary answers it; SEED_STRIP is the part of it the strip shows.
export const SEED = {
  active_bees: 5,
  cells_active: 3,
  cells_inactive: 2,
  cells_real: 1,
  cells_virtual: 4,
  bees_awake: 2,
  bees_autopilot: 3,
  as_of: '2026-01-01T00:00:00Z',
  active_wardens: 1,
  active_sub_bees: 1,
  cells_total: 5,
  open_alarms: 0,
  activity_window_s: 60,
};

export const SEED_STRIP = {
  active_bees: SEED.active_bees,
  cells_active: SEED.cells_active,
  cells_inactive: SEED.cells_inactive,
  cells_real: SEED.cells_real,
  cells_virtual: SEED.cells_virtual,
  bees_awake: SEED.bees_awake,
  bees_autopilot: SEED.bees_autopilot,
  as_of: SEED.as_of,
};
