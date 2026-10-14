import type { WsServerMessage } from './types';

export type ConnectionState = 'connecting' | 'open' | 'closed';

export interface ConsoleSocket {
  /** Send a console command to the server. */
  sendCommand(command: string): void;
  /** Close the socket and stop reconnection. */
  close(): void;
}

export interface ConsoleSocketHandlers {
  onMessage: (msg: WsServerMessage) => void;
  onStateChange: (state: ConnectionState) => void;
}
