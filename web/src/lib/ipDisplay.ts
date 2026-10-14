// Pure display logic for IP values that may be untrusted (recorded before the
// 2026-10-09 PROXY protocol rollout). Separated from the React component so the
// styling decision is unit-testable.

export const UNTRUSTED_IP_TITLE = '启用真实 IP 前的记录';

export interface IpDisplay {
  text: string;
  /** When true: render gray + italic and attach the UNTRUSTED_IP_TITLE tooltip. */
  untrusted: boolean;
  /** Tailwind classes for the <span>. Monospace always; gray italic when untrusted. */
  className: string;
}

/**
 * Decide how to display an IP. `null` ip => placeholder, never "untrusted".
 * A present-but-untrusted IP is grayed and italicized with a tooltip.
 */
export function ipDisplay(ip: string | null, trusted: boolean): IpDisplay {
  if (ip === null) {
    return { text: '—', untrusted: false, className: 'font-mono text-slate-400' };
  }
  if (!trusted) {
    return {
      text: ip,
      untrusted: true,
      className: 'font-mono italic text-slate-400 dark:text-slate-500',
    };
  }
  return { text: ip, untrusted: false, className: 'font-mono text-slate-700 dark:text-slate-300' };
}
