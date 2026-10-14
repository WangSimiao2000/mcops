import { describe, expect, it } from 'vitest';
import type { NotifySettings } from '@/api/types';
import {
  DEFAULT_PORT,
  invalidRecipients,
  isDirty,
  isValidEmail,
  parseRecipients,
  portForSecurity,
  toForm,
  toggleSeverity,
  toPayload,
} from '@/lib/notify';

function baseData(): NotifySettings {
  return {
    email: {
      enabled: true,
      host: 'smtp.qq.com',
      port: 465,
      security: 'ssl',
      username: '',
      sender: 'a@b.com',
      to: ['x@y.com'],
      severities: ['critical', 'warning'],
      max_per_hour: 20,
      has_password: true,
    },
    qq: {
      enabled: false,
      severities: ['critical'],
      groups: [{ openid: 'OPENID_AAA', label: '管理群' }],
    },
    rules: [
      {
        id: 'night_login',
        name: '凌晨登录',
        description: '',
        default_severity: 'warning',
        enabled: true,
        severity: null,
      },
    ],
    known_groups: [
      { openid: 'OPENID_AAA', openid_masked: 'OPEN..._AAA', added_at: null, is_alert: true },
      { openid: 'OPENID_BBB', openid_masked: 'OPEN..._BBB', added_at: null, is_alert: false },
    ],
    bot: { enabled: false, connected: false },
    stats: { email_sent_hour: 0, email_dropped: 0, qq_sent_today: 0, last_error: { email: null, qq: null } },
  };
}

describe('parseRecipients', () => {
  it('splits on comma / newline and trims + dedupes case-insensitively', () => {
    expect(parseRecipients(' a@x.com, b@x.com\n a@x.com ,,A@X.com')).toEqual([
      'a@x.com',
      'b@x.com',
    ]);
  });
  it('handles Chinese separators and empty input', () => {
    expect(parseRecipients('a@x.com，b@x.com、c@x.com')).toEqual(['a@x.com', 'b@x.com', 'c@x.com']);
    expect(parseRecipients('   ')).toEqual([]);
  });
});

describe('isValidEmail / invalidRecipients', () => {
  it('accepts one @, rejects otherwise', () => {
    expect(isValidEmail('a@b.com')).toBe(true);
    expect(isValidEmail('ab.com')).toBe(false);
    expect(isValidEmail('a@@b')).toBe(false);
    expect(isValidEmail('a @b.com')).toBe(false);
  });
  it('reports only the bad ones', () => {
    expect(invalidRecipients('ok@x.com, nope, also@y.com')).toEqual(['nope']);
  });
});

describe('portForSecurity', () => {
  it('fills default when empty', () => {
    expect(portForSecurity('ssl', '')).toBe(DEFAULT_PORT.ssl);
    expect(portForSecurity('starttls', '')).toBe(587);
  });
  it('follows when sitting on the other mode default', () => {
    expect(portForSecurity('starttls', 465)).toBe(587);
    expect(portForSecurity('ssl', 587)).toBe(465);
  });
  it('keeps a custom port', () => {
    expect(portForSecurity('ssl', 2525)).toBe(2525);
  });
});

describe('toggleSeverity', () => {
  it('adds and removes keeping fixed order', () => {
    expect(toggleSeverity(['warning'], 'critical')).toEqual(['critical', 'warning']);
    expect(toggleSeverity(['critical', 'warning'], 'warning')).toEqual(['critical']);
  });
});

describe('toForm / toPayload', () => {
  it('round-trips without a password field by default', () => {
    const data = baseData();
    const form = toForm(data);
    const payload = toPayload(form, data);
    expect('password' in payload.email).toBe(false);
    expect(payload.email.to).toEqual(['x@y.com']);
    expect(payload.qq.groups).toEqual([{ openid: 'OPENID_AAA', label: '管理群' }]);
    expect(payload.rules.night_login).toEqual({ enabled: true, severity: null });
  });

  it('includes password as a string once typed', () => {
    const data = baseData();
    const form = toForm(data);
    form.email.password = 'authcode123';
    const payload = toPayload(form, data);
    expect(payload.email.password).toBe('authcode123');
  });

  it('includes empty-string password to clear it', () => {
    const data = baseData();
    const form = toForm(data);
    form.email.password = '';
    const payload = toPayload(form, data);
    expect(payload.email.password).toBe('');
  });

  it('parses the recipients textarea into a clean array', () => {
    const data = baseData();
    const form = toForm(data);
    form.email.toRaw = 'one@x.com\n two@x.com , one@x.com';
    const payload = toPayload(form, data);
    expect(payload.email.to).toEqual(['one@x.com', 'two@x.com']);
  });
});

describe('isDirty', () => {
  it('is false for a fresh form', () => {
    const data = baseData();
    expect(isDirty(toForm(data), data)).toBe(false);
  });
  it('is true after typing a password', () => {
    const data = baseData();
    const form = toForm(data);
    form.email.password = 'x';
    expect(isDirty(form, data)).toBe(true);
  });
  it('is true after a rule override change', () => {
    const data = baseData();
    const form = toForm(data);
    form.rules.night_login = { enabled: true, severity: 'critical' };
    expect(isDirty(form, data)).toBe(true);
  });
  it('is true after toggling a group', () => {
    const data = baseData();
    const form = toForm(data);
    form.qq.groups['OPENID_BBB'] = '';
    expect(isDirty(form, data)).toBe(true);
  });
});
