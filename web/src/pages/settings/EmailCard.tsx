import type { EmailSecurity } from '@/api/types';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { invalidRecipients, portForSecurity, type NotifyForm } from '@/lib/notify';
import { SeverityPicker, Toggle, TestButton } from './controls';

export function EmailCard({
  form,
  setForm,
  dirty,
}: {
  form: NotifyForm;
  setForm: (fn: (f: NotifyForm) => NotifyForm) => void;
  dirty: boolean;
}) {
  const e = form.email;
  const bad = invalidRecipients(e.toRaw);
  const setEmail = (patch: Partial<NotifyForm['email']>) =>
    setForm((f) => ({ ...f, email: { ...f.email, ...patch } }));

  return (
    <Card>
      <CardHeader
        title="邮件"
        action={
          <Toggle
            id="email-enabled"
            label={e.enabled ? '已启用' : '已停用'}
            checked={e.enabled}
            onChange={(v) => setEmail({ enabled: v })}
          />
        }
      />
      <CardBody className="space-y-4">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Input
            label="SMTP 主机"
            value={e.host}
            placeholder="smtp.qq.com"
            onChange={(ev) => setEmail({ host: ev.target.value })}
          />
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <label
                htmlFor="email-security"
                className="block text-sm font-medium text-slate-700 dark:text-slate-200"
              >
                加密方式
              </label>
              <select
                id="email-security"
                value={e.security}
                onChange={(ev) => {
                  const security = ev.target.value as EmailSecurity;
                  setEmail({ security, port: portForSecurity(security, e.port) });
                }}
                className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 focus:border-brand focus:ring-0 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
              >
                <option value="ssl">SSL (465)</option>
                <option value="starttls">STARTTLS (587)</option>
              </select>
            </div>
            <Input
              label="端口"
              type="number"
              value={e.port === '' ? '' : String(e.port)}
              onChange={(ev) =>
                setEmail({ port: ev.target.value === '' ? '' : Number(ev.target.value) })
              }
            />
          </div>
          <Input
            label="用户名"
            value={e.username}
            hint="留空 = 使用发件地址"
            onChange={(ev) => setEmail({ username: ev.target.value })}
          />
          <Input
            label="发件地址"
            value={e.sender}
            placeholder="mc-ops@example.com"
            onChange={(ev) => setEmail({ sender: ev.target.value })}
          />
        </div>

        <div className="space-y-1">
          <label
            htmlFor="email-to"
            className="block text-sm font-medium text-slate-700 dark:text-slate-200"
          >
            收件人
          </label>
          <textarea
            id="email-to"
            rows={2}
            value={e.toRaw}
            placeholder="多个地址用逗号或换行分隔"
            onChange={(ev) => setEmail({ toRaw: ev.target.value })}
            className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400 focus:border-brand focus:ring-0 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
          />
          {bad.length > 0 ? (
            <p role="alert" className="text-sm text-red-600 dark:text-red-400">
              地址格式不正确：{bad.join('、')}
            </p>
          ) : (
            <p className="text-xs text-slate-500 dark:text-slate-400">
              多个地址用逗号或换行分隔，提交时自动去空白去重。
            </p>
          )}
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Input
            label={`授权码/密码（${e.password !== null ? '将更新' : form_hasPassword(form) ? '已设置' : '未设置'}）`}
            type="password"
            value={e.password ?? ''}
            placeholder={form_hasPassword(form) ? '留空=不修改' : '尚未设置'}
            hint="QQ 邮箱/163 需使用 SMTP 授权码而不是登录密码"
            onChange={(ev) => setEmail({ password: ev.target.value })}
          />
          <Input
            label="每小时上限"
            type="number"
            value={e.max_per_hour === '' ? '' : String(e.max_per_hour)}
            hint="超过后多余邮件被丢弃，避免风暴"
            onChange={(ev) =>
              setEmail({
                max_per_hour: ev.target.value === '' ? '' : Number(ev.target.value),
              })
            }
          />
        </div>

        <div className="space-y-1">
          <span className="block text-sm font-medium text-slate-700 dark:text-slate-200">
            接收级别
          </span>
          <SeverityPicker
            idPrefix="email"
            value={e.severities}
            onChange={(severities) => setEmail({ severities })}
          />
        </div>

        <TestButton channel="email" disabled={!e.enabled} dirty={dirty} />
      </CardBody>
    </Card>
  );
}

/** has_password 的本地镜像：保存前读服务端值，这里从挂在 form 上的快照取。 */
function form_hasPassword(form: NotifyForm): boolean {
  return form.__hasPassword === true;
}
