import { createContext, useContext } from 'react';
import type { ConnectionState } from '@/api/consoleSocket';
import type { AppEvent, Alert, ServerStatus } from '@/api/types';

export interface LogEntry {
  seq: number;
  ts: string;
  line: string;
}

export interface ConsoleContextValue {
  connection: ConnectionState;
  logs: LogEntry[];
  lastStatus: ServerStatus | null;
  lastEvent: AppEvent | null;
  /** Most recent alert pushed over the socket (with a monotonically bumped seq). */
  lastAlert: { alert: Alert; seq: number } | null;
  commandResults: { command: string; output: string; seq: number }[];
  sendCommand: (command: string) => void;
}

export const ConsoleContext = createContext<ConsoleContextValue | null>(null);

export function useConsole(): ConsoleContextValue {
  const ctx = useContext(ConsoleContext);
  if (!ctx) throw new Error('useConsole must be used within ConsoleProvider');
  return ctx;
}
