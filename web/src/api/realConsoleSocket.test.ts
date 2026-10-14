import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createConsoleSocket } from '@/api/realConsoleSocket';
import type { ConnectionState } from '@/api/consoleSocket';

// Minimal fake WebSocket capturing instances so the test can drive open/close.
class FakeWebSocket {
  static OPEN = 1;
  static instances: FakeWebSocket[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  sent: string[] = [];
  closed = false;

  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }
  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.closed = true;
    this.onclose?.();
  }
  simulateOpen() {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }
  simulateServerClose() {
    this.onclose?.();
  }
}

describe('createConsoleSocket reconnect', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeWebSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeWebSocket as unknown as typeof WebSocket);
    vi.spyOn(Math, 'random').mockReturnValue(1); // deterministic full-jitter
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { protocol: 'http:', host: '127.0.0.1:5173' },
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('reports open then reconnects after backoff on unexpected close', () => {
    const states: ConnectionState[] = [];
    createConsoleSocket({
      onStateChange: (s) => states.push(s),
      onMessage: () => {},
    });

    expect(FakeWebSocket.instances).toHaveLength(1);
    FakeWebSocket.instances[0]!.simulateOpen();
    expect(states).toContain('open');

    // Server drops the connection.
    FakeWebSocket.instances[0]!.simulateServerClose();
    // Not yet reconnected (timer pending).
    expect(FakeWebSocket.instances).toHaveLength(1);

    // First reconnect delay with random=1 is ~1000ms.
    vi.advanceTimersByTime(1000);
    expect(FakeWebSocket.instances).toHaveLength(2);
    expect(states[states.length - 1]).toBe('connecting');
  });

  it('does not reconnect after an explicit close()', () => {
    const states: ConnectionState[] = [];
    const sock = createConsoleSocket({
      onStateChange: (s) => states.push(s),
      onMessage: () => {},
    });
    FakeWebSocket.instances[0]!.simulateOpen();
    sock.close();
    expect(states[states.length - 1]).toBe('closed');

    vi.advanceTimersByTime(60_000);
    expect(FakeWebSocket.instances).toHaveLength(1); // no new sockets
  });

  it('sends commands only when the socket is open', () => {
    const sock = createConsoleSocket({ onStateChange: () => {}, onMessage: () => {} });
    const ws = FakeWebSocket.instances[0]!;
    sock.sendCommand('list'); // not open yet -> dropped
    expect(ws.sent).toHaveLength(0);
    ws.simulateOpen();
    sock.sendCommand('list');
    expect(ws.sent).toEqual([JSON.stringify({ kind: 'command', command: 'list' })]);
  });
});
