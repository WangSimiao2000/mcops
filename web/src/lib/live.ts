import type { QueryClient, QueryKey } from '@tanstack/react-query';
import type { WsServerMessage } from '@/api/types';

/**
 * Data that changes on its own is refreshed by WebSocket pushes (see
 * `keysForMessage`); polling is only a safety net for missed frames.
 */
export const FALLBACK_REFETCH_MS = 120_000;

/** Server-side resource gauges (CPU / memory) have no push source, so they keep a short poll. */
export const GAUGE_REFETCH_MS = 15_000;

const SERVER_KEYS: QueryKey[] = [['serverStatus'], ['system'], ['events'], ['jobs']];

/**
 * Map one WebSocket frame to the query keys whose data it makes stale.
 * Keys are prefixes: ['players'] also refreshes ['players', q].
 */
export function keysForMessage(msg: WsServerMessage): QueryKey[] {
  switch (msg.kind) {
    case 'alert':
      return [['alerts'], ['securityRules'], ['events']];
    case 'event': {
      const t = msg.event.type;
      if (t === 'player_login' || t === 'player_leave' || t === 'player_uuid') {
        return [['players'], ['player'], ['events'], ['serverStatus'], ['network', 'ips']];
      }
      if (t === 'job_started' || t === 'job_finished') {
        return [['jobs'], ['backups'], ['restic'], ['events']];
      }
      if (t === 'bot_interaction') {
        return [['botApplications'], ['botInteractions'], ['botStatus'], ['whitelist']];
      }
      if (t === 'ip_banned' || t === 'ip_unbanned' || t === 'ban_ip') {
        return [['network'], ['ip'], ['events']];
      }
      if (t.startsWith('server_') || t === 'agent_started') return SERVER_KEYS;
      return [['events']];
    }
    default:
      // log lines, status frames (written straight into the cache by the overview) and
      // command results carry no list data.
      return [];
  }
}

/** Invalidate (refetch only if mounted) everything a frame makes stale. */
export function invalidateForMessage(qc: QueryClient, msg: WsServerMessage): void {
  for (const queryKey of keysForMessage(msg)) {
    void qc.invalidateQueries({ queryKey });
  }
}
