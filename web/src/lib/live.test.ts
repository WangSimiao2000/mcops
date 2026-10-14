import { QueryClient } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';
import type { WsServerMessage } from '@/api/types';
import { invalidateForMessage, keysForMessage } from './live';

function ev(type: string): WsServerMessage {
  return { kind: 'event', event: { id: 1, ts: '2026-10-09T00:00:00Z', type, source: 't', data: {} } };
}

describe('keysForMessage', () => {
  it('maps pushed events to the lists they make stale', () => {
    expect(keysForMessage(ev('player_login'))).toContainEqual(['players']);
    expect(keysForMessage(ev('job_finished'))).toEqual(
      expect.arrayContaining([['jobs'], ['backups'], ['restic']]),
    );
    expect(keysForMessage(ev('bot_interaction'))).toEqual(
      expect.arrayContaining([['botApplications'], ['whitelist']]),
    );
    expect(keysForMessage(ev('ip_banned'))).toContainEqual(['network']);
    expect(keysForMessage(ev('server_crashed'))).toContainEqual(['serverStatus']);
    expect(keysForMessage(ev('something_new'))).toEqual([['events']]);
  });

  it('refreshes alert views on a pushed alert', () => {
    const msg = { kind: 'alert', alert: {} } as unknown as WsServerMessage;
    expect(keysForMessage(msg)).toContainEqual(['alerts']);
  });

  it('ignores log lines and status frames', () => {
    expect(keysForMessage({ kind: 'log', ts: 'x', line: 'y' } as WsServerMessage)).toEqual([]);
  });
});

describe('invalidateForMessage', () => {
  it('invalidates by prefix', () => {
    const qc = new QueryClient();
    const spy = vi.spyOn(qc, 'invalidateQueries');
    invalidateForMessage(qc, ev('player_leave'));
    expect(spy).toHaveBeenCalledWith({ queryKey: ['players'] });
  });
});
