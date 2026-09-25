/**
 * Games Service - Manages game engines and tables
 * Supports Teen Patti Pro, Greedy Lion, and Monkey Wheel
 */
import { GameCode, GameBinding, BINDINGS, canonical_code } from '../types';
import { ProviderContext } from './context';

export interface GameBinding {
  game_code: string;
  label: string;
  kind: 'table_game' | 'wheel';
  action_field: string;
  choice_field: string;
  service: (ctx: any) => any;
  default_tables: string[];
}

export interface GameService {
  start_round(room_id: string, actor?: string): Promise<any>;
  close_betting(room_id: string): Promise<any>;
  publish_result(room_id: string): Promise<any>;
  settle(room_id: string): Promise<any>;
  cancel_round(room_id: string, reason: string, actor: string): Promise<any>;
  place_bet(room_id: string, player_id: string, option_id: string, amount: number, idempotency_key: string): Promise<any>;
  state(room_id: string, player_id: string): Promise<any>;
  history(room_id: string, player_id: string, limit: number): Promise<any>;
  ensure_round(room_id: string): Promise<any>;
  leave_table(room_id: string, player_id: string): Promise<any>;
}

export const BINDINGS: Record<string, any> = {
  teen_patti_pro: {
    game_code: 'teen_patti_pro',
    label: 'Teen Patti Pro',
    kind: 'table_game',
    action_field: 'position',
    choice_field: 'position',
    default_tables: ['teen-patti-low', 'teen-patti-mid', 'teen-patti-high'],
  },
  greedy_lion: {
    game_code: 'greedy_lion',
    label: 'Greedy Lion',
    kind: 'wheel',
    action_field: 'option_id',
    choice_field: 'option_id',
    default_tables: ['greedy-lion-low', 'greedy-lion-mid', 'greedy-lion-high'],
  },
  monkey_wheel: {
    game_code: 'monkey_wheel',
    label: 'Monkey Wheel',
    kind: 'wheel',
    action_field: 'option_id',
    choice_field: 'option_id',
    default_tables: ['monkey-wheel-low', 'monkey-wheel-mid', 'monkey-wheel-high'],
  },
};

export function canonical_code(raw: string): string {
  const code = String(raw || 'teen_patti_pro').toLowerCase();
  if (['teen_patti_pro', 'teen-patti-pro', 'teenpatti'].includes(code)) return 'teen_patti_pro';
  if (['greedy-lion', 'greedy_lion'].includes(code)) return 'greedy_lion';
  if (['monkey-wheel', 'monkey_wheel', 'greedy-monkey', 'greedy_monkey'].includes(code)) return 'monkey_wheel';
  return 'teen_patti_pro';
}

export function binding_for_code(code: string) {
  return canonical_code(code);
}

export function binding_for_slug(slug: string) {
  for (const binding of Object.values(BINDINGS)) {
    if (binding.slug === slug) return binding;
  }
  return null;
}
