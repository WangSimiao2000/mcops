import { describe, expect, it } from 'vitest';
import { classifyLogLine, colorizeLogLine, SEVERITY_SGR } from '@/lib/logColor';

describe('classifyLogLine', () => {
  it('classifies ERROR/FATAL lines as error', () => {
    expect(classifyLogLine('[Server thread/ERROR]: boom')).toBe('error');
    expect(classifyLogLine('java.lang.NullPointerException: x')).toBe('error');
    expect(classifyLogLine('Caused by: something')).toBe('error');
  });

  it('classifies WARN lines as warn', () => {
    expect(classifyLogLine("[Server thread/WARN]: Can't keep up!")).toBe('warn');
    expect(classifyLogLine('WARNING deprecated')).toBe('warn');
  });

  it('error wins over warn when both appear', () => {
    expect(classifyLogLine('WARN but also ERROR here')).toBe('error');
  });

  it('highlights login/leave lines', () => {
    expect(classifyLogLine('OpAdmin joined the game')).toBe('login');
    expect(classifyLogLine('Zoe left the game')).toBe('login');
    expect(classifyLogLine('UUID of player X is ... logged in with entity id 5')).toBe('login');
  });

  it('falls back to info', () => {
    expect(classifyLogLine('[Server thread/INFO]: Done (3.1s)!')).toBe('info');
  });
});

describe('colorizeLogLine', () => {
  it('leaves info lines unwrapped', () => {
    const line = 'plain info line';
    expect(colorizeLogLine(line)).toBe(line);
  });

  it('wraps error lines in red SGR with reset', () => {
    const out = colorizeLogLine('[ERROR] boom');
    expect(out.startsWith(SEVERITY_SGR.error)).toBe(true);
    expect(out.endsWith('\u001b[0m')).toBe(true);
  });

  it('wraps warn lines in yellow SGR', () => {
    expect(colorizeLogLine('[WARN] slow').startsWith(SEVERITY_SGR.warn)).toBe(true);
  });
});
