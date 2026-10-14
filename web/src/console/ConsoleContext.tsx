import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import type { ReactNode } from 'react';
import { createConsoleSocket } from '@/api';
import type { ConnectionState, ConsoleSocket } from '@/api/consoleSocket';
import { useQueryClient } from '@tanstack/react-query';
import type { AppEvent, Alert, ServerStatus, WsServerMessage } from '@/api/types';
import { useAuth } from '@/auth/useAuth';
import { invalidateForMessage } from '@/lib/live';
import { ConsoleContext } from './useConsole';
import type { ConsoleContextValue, LogEntry } from './useConsole';

const MAX_LOGS = 2000;

/**
 * Owns the single console WebSocket for the authenticated app. Mounted inside
 * the protected layout so the socket exists only while logged in; the topbar
 * reads `connection` and the console page reads logs/results.
 */
export function ConsoleProvider({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  const qc = useQueryClient();
  const [connection, setConnection] = useState<ConnectionState>('closed');
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [lastStatus, setLastStatus] = useState<ServerStatus | null>(null);
  const [lastEvent, setLastEvent] = useState<AppEvent | null>(null);
  const [lastAlert, setLastAlert] = useState<{ alert: Alert; seq: number } | null>(null);
  const [commandResults, setCommandResults] = useState<
    { command: string; output: string; seq: number }[]
  >([]);
  const socketRef = useRef<ConsoleSocket | null>(null);
  const seqRef = useRef(0);

  useEffect(() => {
    if (status !== 'authenticated') return;
    let wasOpen = false;
    const socket = createConsoleSocket({
      onStateChange: (state) => {
        setConnection(state);
        // Frames pushed while we were disconnected are lost: refetch everything once on reconnect.
        if (state === 'open') {
          if (wasOpen) void qc.invalidateQueries();
          wasOpen = true;
        }
      },
      onMessage: (msg: WsServerMessage) => {
        invalidateForMessage(qc, msg);
        switch (msg.kind) {
          case 'log': {
            seqRef.current += 1;
            const entry: LogEntry = { seq: seqRef.current, ts: msg.ts, line: msg.line };
            setLogs((prev) => {
              const next = prev.length >= MAX_LOGS ? prev.slice(prev.length - MAX_LOGS + 1) : prev;
              return [...next, entry];
            });
            break;
          }
          case 'status':
            setLastStatus(msg.status);
            break;
          case 'event':
            setLastEvent(msg.event);
            break;
          case 'alert': {
            seqRef.current += 1;
            const seq = seqRef.current;
            setLastAlert({ alert: msg.alert, seq });
            break;
          }
          case 'command_result': {
            seqRef.current += 1;
            const seq = seqRef.current;
            setCommandResults((prev) => [...prev.slice(-50), { command: msg.command, output: msg.output, seq }]);
            break;
          }
        }
      },
    });
    socketRef.current = socket;
    return () => {
      socket.close();
      socketRef.current = null;
    };
  }, [status, qc]);

  const sendCommand = useCallback((command: string) => {
    socketRef.current?.sendCommand(command);
  }, []);

  const value = useMemo<ConsoleContextValue>(
    () => ({ connection, logs, lastStatus, lastEvent, lastAlert, commandResults, sendCommand }),
    [connection, logs, lastStatus, lastEvent, lastAlert, commandResults, sendCommand],
  );

  return <ConsoleContext.Provider value={value}>{children}</ConsoleContext.Provider>;
}
