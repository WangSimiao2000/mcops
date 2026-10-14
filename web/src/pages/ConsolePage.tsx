import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { FitAddon } from '@xterm/addon-fit';
import { Terminal } from '@xterm/xterm';
import '@xterm/xterm/css/xterm.css';
import { api } from '@/api';
import { useConsole } from '@/console/useConsole';
import { ConnectionIndicator } from '@/components/ConnectionIndicator';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { colorizeLogLine } from '@/lib/logColor';
import { isControlDisabled, OBSERVE_NOTICE } from '@/lib/security';
import { useTheme } from '@/hooks/useTheme';
import { FALLBACK_REFETCH_MS } from '@/lib/live';

const LIGHT_THEME = { background: '#f8fafc', foreground: '#0f172a' };
const DARK_THEME = { background: '#020617', foreground: '#e2e8f0' };

export function ConsolePage() {
  const { connection, logs, commandResults, sendCommand } = useConsole();
  const { isDark } = useTheme();
  const systemQuery = useQuery({
    queryKey: ['system'],
    queryFn: ({ signal }) => api.system(signal),
    refetchInterval: FALLBACK_REFETCH_MS,
  });
  const observe = isControlDisabled(systemQuery.data?.mode);
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const writtenLog = useRef(0);
  const writtenResult = useRef(0);

  const [command, setCommand] = useState('');
  const history = useRef<string[]>([]);
  const historyIdx = useRef(-1);

  // Create the terminal once.
  useEffect(() => {
    if (!containerRef.current) return;
    const term = new Terminal({
      convertEol: true,
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
      fontSize: 13,
      theme: isDark ? DARK_THEME : LIGHT_THEME,
      scrollback: 5000,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(containerRef.current);
    termRef.current = term;
    fitRef.current = fit;

    // The renderer measures its cell size asynchronously after open(); fitting before that
    // throws "Cannot read properties of undefined (reading 'dimensions')". Fit on the next
    // frame and whenever the container changes size (window resize, mobile drawer, rotation).
    let frame = 0;
    const safeFit = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        try {
          fit.fit();
        } catch {
          // Not measurable yet (hidden / zero size); the next resize retries.
        }
      });
    };
    safeFit();
    const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(safeFit);
    ro?.observe(containerRef.current);
    window.addEventListener('resize', safeFit);
    return () => {
      ro?.disconnect();
      window.removeEventListener('resize', safeFit);
      cancelAnimationFrame(frame);
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
      writtenLog.current = 0;
      writtenResult.current = 0;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // React to theme changes.
  useEffect(() => {
    if (termRef.current) {
      termRef.current.options.theme = isDark ? DARK_THEME : LIGHT_THEME;
    }
  }, [isDark]);

  // Stream new log lines into the terminal.
  useEffect(() => {
    const term = termRef.current;
    if (!term) return;
    for (let i = writtenLog.current; i < logs.length; i += 1) {
      term.writeln(colorizeLogLine(logs[i]!.line));
    }
    writtenLog.current = logs.length;
    term.scrollToBottom();
  }, [logs]);

  // Echo command results.
  useEffect(() => {
    const term = termRef.current;
    if (!term) return;
    for (let i = writtenResult.current; i < commandResults.length; i += 1) {
      const r = commandResults[i]!;
      term.writeln(`\u001b[36m> ${r.command}\u001b[0m`);
      term.writeln(r.output);
    }
    writtenResult.current = commandResults.length;
    term.scrollToBottom();
  }, [commandResults]);

  function submit() {
    if (observe) return;
    const cmd = command.trim();
    if (!cmd) return;
    sendCommand(cmd);
    history.current.push(cmd);
    historyIdx.current = history.current.length;
    setCommand('');
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter') {
      e.preventDefault();
      submit();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (history.current.length === 0) return;
      historyIdx.current = Math.max(0, historyIdx.current - 1);
      setCommand(history.current[historyIdx.current] ?? '');
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (history.current.length === 0) return;
      historyIdx.current = Math.min(history.current.length, historyIdx.current + 1);
      setCommand(history.current[historyIdx.current] ?? '');
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">控制台</h1>
        <ConnectionIndicator state={connection} />
      </div>

      <div
        ref={containerRef}
        role="log"
        aria-label="服务器实时日志"
        aria-live="polite"
        className="min-h-0 flex-1 overflow-hidden rounded-lg border border-slate-200 p-2 dark:border-slate-800"
      />

      <form
        className="flex items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <div className="flex-1">
          <Input
            label="直接发送到服务器控制台"
            value={command}
            onChange={(e) => setCommand(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={observe ? '观测模式下命令输入已禁用' : '例如：list（回车发送，↑/↓ 浏览历史）'}
            className="font-mono"
            autoComplete="off"
            spellCheck={false}
            disabled={observe || connection !== 'open'}
            hint={observe ? OBSERVE_NOTICE : undefined}
          />
        </div>
        <Button type="submit" disabled={observe || connection !== 'open' || command.trim() === ''}>
          发送
        </Button>
      </form>
    </div>
  );
}
