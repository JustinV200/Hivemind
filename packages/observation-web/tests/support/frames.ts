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

export const SEED = {
  active_bees: 5,
  cells_active: 3,
  cells_inactive: 2,
  cells_real: 1,
  cells_virtual: 4,
  bees_awake: 2,
  bees_autopilot: 3,
  as_of: '2026-01-01T00:00:00Z',
};
