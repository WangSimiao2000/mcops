import type { ConsoleSocket, ConsoleSocketHandlers } from './consoleSocket';
import { mockPushAlert, mockPushEvent, mockServerStatus } from './mockApi';
import type { WsServerMessage } from './types';

const SAMPLE_LINES = [
  '[Server thread/INFO]: Done (3.142s)! For help, type "help"',
  '[Server thread/INFO]: OpAdmin joined the game',
  '[Server thread/WARN]: Can\'t keep up! Is the server overloaded? Running 2051ms behind',
  '[Server thread/INFO]: <OpAdmin> gg',
  '[Server thread/ERROR]: Encountered an unexpected exception',
  '[Server thread/WARN]: Zoe moved too quickly! 12.4,0,0',
];

/**
 * Fake console socket for VITE_MOCK mode. Immediately reports "open", then
 * streams sample log lines and periodic status updates, and echoes commands
 * back as command_result frames so every console feature can be exercised
 * without a backend.
 */
export function createMockConsoleSocket(handlers: ConsoleSocketHandlers): ConsoleSocket {
  let closed = false;
  const timers: ReturnType<typeof setTimeout>[] = [];
  let lineIdx = 0;

  const emit = (msg: WsServerMessage) => {
    if (!closed) handlers.onMessage(msg);
  };

  // Simulate the async connect handshake.
  const openTimer = setTimeout(() => {
    if (closed) return;
    handlers.onStateChange('open');
    emit({ kind: 'status', status: mockServerStatus() });
  }, 300);
  timers.push(openTimer);

  // Push one live critical alert a few seconds in, so the toast + bell refresh
  // path can be exercised without a backend.
  const alertTimer = setTimeout(() => {
    if (closed) return;
    emit({ kind: 'alert', alert: mockPushAlert() });
  }, 6000);
  timers.push(alertTimer);

  const logTimer = setInterval(() => {
    if (closed) return;
    const line = SAMPLE_LINES[lineIdx % SAMPLE_LINES.length]!;
    lineIdx += 1;
    emit({ kind: 'log', ts: new Date().toISOString(), line });
    if (lineIdx % 4 === 0) {
      emit({
        kind: 'event',
        event: mockPushEvent({
          type: 'player_login',
          source: 'logs',
          data: { name: 'guest' + lineIdx, ip: '192.0.2.' + (lineIdx % 254), port: 50000 + lineIdx },
        }),
      });
    }
  }, 2500);

  handlers.onStateChange('connecting');

  return {
    sendCommand(command: string): void {
      if (closed) return;
      setTimeout(() => emit({ kind: 'command_result', command, output: `[mock] > ${command}` }), 200);
    },
    close(): void {
      closed = true;
      clearInterval(logTimer);
      for (const t of timers) clearTimeout(t);
      handlers.onStateChange('closed');
    },
  };
}
