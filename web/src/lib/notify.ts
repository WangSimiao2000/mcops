// Pure logic for the 「设置 · 通知」 page. No React, fully unit-tested.

import type {
  EmailSecurity,
  NotifyRule,
  NotifySettings,
  NotifySettingsUpdate,
  Sev,
} from '@/api/types';

/** 固定级别顺序，用于多选复选框渲染与去重。 */
export const SEVERITIES: Sev[] = ['critical', 'warning', 'info'];

export const SEV_LABEL: Record<Sev, string> = {
  critical: '严重',
  warning: '警告',
  info: '提示',
};

/** 加密方式 -> 默认端口。 */
export const DEFAULT_PORT: Record<EmailSecurity, number> = {
  ssl: 465,
  starttls: 587,
};

/**
 * 切换加密方式后应落到的端口：仅当当前端口为空或正好是另一方式的默认端口时自动改成本方式默认端口，
 * 否则保留用户手填的端口。
 */
export function portForSecurity(
  security: EmailSecurity,
  currentPort: number | '',
): number {
  const want = DEFAULT_PORT[security];
  if (currentPort === '' || currentPort === 0) return want;
  // 之前停在另一个加密方式的默认端口上 -> 跟随切换。
  const other: EmailSecurity = security === 'ssl' ? 'starttls' : 'ssl';
  if (currentPort === DEFAULT_PORT[other]) return want;
  return currentPort;
}

/**
 * 把收件人输入框（逗号或换行分隔）解析成去空白、去重后的地址数组，
 * 保留首次出现顺序。
 */
export function parseRecipients(raw: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const part of raw.split(/[\n,，、;；]+/)) {
    const addr = part.trim();
    if (!addr) continue;
    const key = addr.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(addr);
  }
  return out;
}

/** 极简邮箱校验：含且仅含一个 @，且两侧非空、无空白。 */
export function isValidEmail(addr: string): boolean {
  return /^[^\s@]+@[^\s@]+$/.test(addr);
}

/** 返回解析后不合法的收件地址（用于前端提示）。 */
export function invalidRecipients(raw: string): string[] {
  return parseRecipients(raw).filter((a) => !isValidEmail(a));
}

/** 在多选集合里切换一个级别，输出按固定顺序排序的新数组。 */
export function toggleSeverity(current: Sev[], sev: Sev): Sev[] {
  const set = new Set(current);
  if (set.has(sev)) set.delete(sev);
  else set.add(sev);
  return SEVERITIES.filter((s) => set.has(s));
}

/** 规则覆盖下拉的候选项（"默认" 对应 null）。 */
export function ruleOverrideOptions(): { value: '' | Sev; label: string }[] {
  return [
    { value: '', label: '默认' },
    { value: 'critical', label: SEV_LABEL.critical },
    { value: 'warning', label: SEV_LABEL.warning },
    { value: 'info', label: SEV_LABEL.info },
  ];
}

/** 规则生效级别：有覆盖用覆盖，否则用默认级别。 */
export function effectiveSeverity(rule: NotifyRule): Sev {
  return rule.severity ?? rule.default_severity;
}

// --- 表单可编辑状态（受控表单的本地镜像） ------------------------------------

export interface NotifyForm {
  email: {
    enabled: boolean;
    host: string;
    port: number | '';
    security: EmailSecurity;
    username: string;
    sender: string;
    /** 原始收件人输入（逗号/换行分隔），仅在提交时解析。 */
    toRaw: string;
    severities: Sev[];
    max_per_hour: number | '';
    /** 用户新输入的密码；null = 未输入（不修改）。 */
    password: string | null;
  };
  qq: {
    enabled: boolean;
    severities: Sev[];
    /** openid -> 备注 label。勾选 = 在此 map 中出现。 */
    groups: Record<string, string>;
  };
  /** rule id -> { enabled, severity 覆盖 }。 */
  rules: Record<string, { enabled: boolean; severity: Sev | null }>;
  /** 服务端 has_password 的只读快照，仅用于密码框显示"已设置/未设置"，不进 payload。 */
  __hasPassword?: boolean;
}

/** 从服务端响应构造可编辑表单（密码框永远从空开始）。 */
export function toForm(data: NotifySettings): NotifyForm {
  const groups: Record<string, string> = {};
  for (const g of data.qq.groups) groups[g.openid] = g.label;
  const rules: NotifyForm['rules'] = {};
  for (const r of data.rules) {
    rules[r.id] = { enabled: r.enabled, severity: r.severity };
  }
  return {
    email: {
      enabled: data.email.enabled,
      host: data.email.host,
      port: data.email.port,
      security: data.email.security,
      username: data.email.username,
      sender: data.email.sender,
      toRaw: data.email.to.join('\n'),
      severities: [...data.email.severities],
      max_per_hour: data.email.max_per_hour,
      password: null,
    },
    qq: {
      enabled: data.qq.enabled,
      severities: [...data.qq.severities],
      groups,
    },
    rules,
  };
}

/**
 * 把表单转成 PUT 请求体。
 * - 收件人解析去空白去重；
 * - port / max_per_hour 空值回落到合理默认；
 * - password === null 时省略该字段（不修改）；"" 清除；非空设置。
 */
export function toPayload(
  form: NotifyForm,
  data: NotifySettings,
): NotifySettingsUpdate {
  const emailBase = {
    enabled: form.email.enabled,
    host: form.email.host.trim(),
    port:
      form.email.port === '' || form.email.port === 0
        ? DEFAULT_PORT[form.email.security]
        : form.email.port,
    security: form.email.security,
    username: form.email.username.trim(),
    sender: form.email.sender.trim(),
    to: parseRecipients(form.email.toRaw),
    severities: form.email.severities,
    max_per_hour:
      form.email.max_per_hour === '' ? 0 : form.email.max_per_hour,
  };
  const email =
    form.email.password === null
      ? emailBase
      : { ...emailBase, password: form.email.password };

  // 已知群 + 当前仍勾选但不在已知群中的历史群，都要在 groups 下拉里出现。
  const knownOpenids = new Set(data.known_groups.map((g) => g.openid));
  const groups = Object.entries(form.qq.groups).map(([openid, label]) => ({
    openid,
    label: label.trim(),
  }));
  // 稳定排序：已知群优先，其余保持插入顺序。
  groups.sort((a, b) => {
    const ka = knownOpenids.has(a.openid) ? 0 : 1;
    const kb = knownOpenids.has(b.openid) ? 0 : 1;
    return ka - kb;
  });

  return {
    email,
    qq: {
      enabled: form.qq.enabled,
      severities: form.qq.severities,
      groups,
    },
    rules: form.rules,
  };
}

/** 深比较表单与服务端，判断是否有未保存修改（密码已输入即视为有修改）。 */
export function isDirty(form: NotifyForm, data: NotifySettings): boolean {
  if (form.email.password !== null) return true;
  const a = JSON.stringify(stripPassword(toPayload(form, data)));
  const b = JSON.stringify(stripPassword(toPayload(toForm(data), data)));
  return a !== b;
}

function stripPassword(p: NotifySettingsUpdate): NotifySettingsUpdate {
  const { password: _ignored, ...email } = p.email;
  return { ...p, email };
}
