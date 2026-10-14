import { describe, expect, it } from 'vitest';
import { ipDisplay, UNTRUSTED_IP_TITLE } from '@/lib/ipDisplay';

describe('ipDisplay', () => {
  it('renders a placeholder for a null IP and never marks it untrusted', () => {
    const d = ipDisplay(null, false);
    expect(d.text).toBe('—');
    expect(d.untrusted).toBe(false);
  });

  it('marks a pre-PROXY IP untrusted: gray, italic, with tooltip', () => {
    const d = ipDisplay('127.0.0.1', false);
    expect(d.text).toBe('127.0.0.1');
    expect(d.untrusted).toBe(true);
    expect(d.className).toContain('italic');
    expect(d.className).toContain('text-slate-400');
  });

  it('shows a trusted IP in normal (non-italic) style', () => {
    const d = ipDisplay('203.0.113.17', true);
    expect(d.untrusted).toBe(false);
    expect(d.className).not.toContain('italic');
  });

  it('exposes the tooltip text for untrusted records', () => {
    expect(UNTRUSTED_IP_TITLE).toBe('启用真实 IP 前的记录');
  });
});
