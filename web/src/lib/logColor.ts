export type LogSeverity = 'error' | 'warn' | 'login' | 'info';

const ERROR_RE = /\b(ERROR|FATAL|SEVERE)\b|Exception|\bCaused by\b/;
const WARN_RE = /\bWARN(?:ING)?\b/;
const LOGIN_RE = /\b(joined the game|logged in with entity id|lost connection|left the game)\b/i;

/**
 * Classify a raw Minecraft/agent log line into a severity bucket used for
 * terminal coloring. Order matters: ERROR wins over WARN, login highlight is
 * checked before falling back to info.
 */
export function classifyLogLine(line: string): LogSeverity {
  if (ERROR_RE.test(line)) return 'error';
  if (WARN_RE.test(line)) return 'warn';
  if (LOGIN_RE.test(line)) return 'login';
  return 'info';
}

/** xterm SGR color codes per severity (foreground). */
export const SEVERITY_SGR: Record<LogSeverity, string> = {
  error: '\u001b[31m', // red
  warn: '\u001b[33m', // yellow
  login: '\u001b[36m', // cyan highlight for login/leave events
  info: '\u001b[0m', // default
};

const RESET = '\u001b[0m';

/** Wrap a log line in the SGR codes for its severity. */
export function colorizeLogLine(line: string): string {
  const sev = classifyLogLine(line);
  if (sev === 'info') return line;
  return `${SEVERITY_SGR[sev]}${line}${RESET}`;
}
