import { backoffDelay } from './backoff';
import type {
  ConnectionState,
  ConsoleSocket,
  ConsoleSocketHandlers,
} from './consoleSocket';
import type { WsCommandMessage, WsServerMessage } from './types';

function wsUrl(path: string): string {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${window.location.host}${path}`;
}

/**
 * Live console socket backed by a real WebSocket.
 * Reconnects automatically with exponential backoff (cap 30s) on unexpected
 * close, surfacing connection state through onStateChange.
 */
export function createConsoleSocket(handlers: ConsoleSocketHandlers): ConsoleSocket {
  let ws: WebSocket | null = null;
  let attempt = 0;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let closedByUser = false;

  function setState(state: ConnectionState): void {
    handlers.onStateChange(state);
  }

  function connect(): void {
    if (closedByUser) return;
    setState('connecting');
    const socket = new WebSocket(wsUrl('/api/ws/console'));
    ws = socket;

    socket.onopen = () => {
      attempt = 0;
      setState('open');
    };

    socket.onmessage = (ev: MessageEvent) => {
      try {
        const parsed = JSON.parse(String(ev.data)) as WsServerMessage;
        handlers.onMessage(parsed);
      } catch {
        // Ignore malformed frames rather than tearing down the socket.
      }
    };

    socket.onclose = () => {
      ws = null;
      if (closedByUser) {
        setState('closed');
        return;
      }
      setState('connecting');
      attempt += 1;
      const delay = backoffDelay(attempt);
      reconnectTimer = setTimeout(connect, delay);
    };

    socket.onerror = () => {
      // onclose will follow; just let it drive reconnection.
      socket.close();
    };
  }

  connect();

  return {
    sendCommand(command: string): void {
      if (ws && ws.readyState === WebSocket.OPEN) {
        const msg: WsCommandMessage = { kind: 'command', command };
        ws.send(JSON.stringify(msg));
      }
    },
    close(): void {
      closedByUser = true;
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      ws?.close();
      ws = null;
      setState('closed');
    },
  };
}
