# mc-ops web

mc-ops 运维面板的前端：登录、总览、实时控制台（M0），玩家画像 / IP 画像 / 安全告警（M1 旁路观测）。

技术栈：Vite + React 18 + TypeScript(strict) + Tailwind CSS + react-router +
@tanstack/react-query + @xterm/xterm。界面为中文，支持亮/暗色（默认跟随系统，可手动切换）。

## 开发

```bash
cd web
npm install          # 首次安装依赖（生成 package-lock.json）
npm run dev          # 启动 Vite dev server (http://127.0.0.1:5173)
```

Dev server 会把 `/api`（含 WebSocket `/api/ws/*`）代理到后端
`http://127.0.0.1:8765`。本地开发时先用 SSH 隧道把后端端口转发到本机：

```bash
ssh -L 8765:127.0.0.1:8765 mc-server
```

## Mock 模式（无需后端）

设置 `VITE_MOCK=1` 时，前端用内置的内存 mock 后端与假 WebSocket 跑通**全部页面**：
登录（密码 `admin`）、服务器状态/启停、模块健康、事件列表、控制台实时日志与命令回显。

```bash
VITE_MOCK=1 npm run dev        # mock 开发
VITE_MOCK=1 npm run build      # mock 构建也应成功
```

mock 的实现见 `src/api/mockApi.ts` 与 `src/api/mockConsoleSocket.ts`；
运行时由 `src/api/index.ts` 依据 `import.meta.env.VITE_MOCK` 选择真实或 mock 实现。

## 截图检查

```bash
npx playwright install chromium   # 首次
npm run screenshots -- /tmp/shots # 默认输出到 web/screenshots/（已忽略）
```

在 mock 模式下把每个页面按「亮色/暗色 × 桌面 1440 / 手机 390」各截一张整页图，
并在 `report.json` 里列出控制台报错和超出视口宽度的元素。

## 脚本

| 命令 | 作用 |
|---|---|
| `npm run dev` | 开发服务器 |
| `npm run build` | `tsc -b` 类型检查后 `vite build`，产物输出 `web/dist` |
| `npm run preview` | 预览构建产物 |
| `npm run typecheck` | `tsc --noEmit` |
| `npm run lint` | ESLint（flat config） |
| `npm run test` | Vitest 单元测试 |

## 构建产物

`npm run build` 输出到 `web/dist`。生产环境由后端（FastAPI）在同一端口以静态文件提供，
并对非 `/api` 路径做 SPA history fallback。前端始终使用相对 `/api` 作为基址，
因此无需配置后端地址。

## 与后端的契约摘要

- **认证**：Cookie 会话。后端登录成功后下发 HttpOnly 的 `mcops_session` 与可读的
  `mcops_csrf`。前端对所有非 GET 请求自动附带请求头 `X-CSRF-Token`（取自
  `mcops_csrf` cookie）。任何接口返回 401 都会触发全局处理器跳转登录页；
  登录接口的 401 由登录页就地处理（`detail === "totp_required"` 时显示 TOTP 输入框）。
- **REST**（基址 `/api`）：
  - `POST /auth/login` `{password, totp?}` -> `{user:"admin"}`；失败 401 `{detail}`，限流 429。
  - `POST /auth/logout` -> 204。
  - `GET /auth/me` -> `{user}` 或 401。
  - `GET /health`（免登录）-> `{status, version}`。
  - `GET /system` -> `{mode:"observe"|"control", modules: {name: {state, detail?, restarts}}}`。
    - `mode="observe"`（M1 生产默认，只读观测）：总览的启停/重启按钮与控制台命令输入禁用，
      显示“观测模式：服务器仍由旧脚本管理”。`mode="control"` 时才开放写操作。
  - `GET /server/status` -> `{state, pid, uptime_s, mc_version, players}`。
  - `POST /server/{start,stop,restart}` -> 202 `{accepted:true}`；409 `{detail}`。
  - `POST /server/command` `{command}` -> `{output}`。
  - `GET /events?limit=&types=` -> `{items: Event[]}`。
- **M1 旁路观测**（基址 `/api`）：
  - `GET /players?q=&limit=200` -> `{items: Player[]}`。
  - `GET /players/{name}` -> `{player, sessions: Session[], ips: PlayerIp[], events: Event[]}`。
  - `GET /sessions?limit=100&online=true|false` -> `{items: Session[]}`。
  - `GET /ips/{ip}` -> `IpInfo`（含 `frps.by_proxy` 的 minecraft/ssh 分项、`banned`）。
  - `GET /alerts?status=open|acked|all&limit=100`
    -> `{items: Alert[], open_counts: {critical, warning, info}}`。
  - `POST /alerts/{id}/ack` -> `{ok:true}`（CSRF）。
  - `POST /alerts/ack-all` `{severity?}` -> `{acked: number}`（CSRF）。
  - `GET /security/rules` -> `{items: Rule[]}`（只读，显示启用状态与严重度）。
  - `GET /edge/status` -> `{enabled, connected, last_event_ts, error, events_24h}`。
  - 关键字段语义：`Session.ip_trusted=false` / `Player.last_ip_trusted=false` / `PlayerIp.trusted=false`
    表示 2026-10-09 启用 PROXY protocol 前记录的 `127.0.0.1`，UI 以灰色斜体 + “启用真实 IP 前的记录”提示；
    `Player.flags` 含 `unregistered_whitelisted`（白名单但 EasyAuth 未注册，高危，红色“未注册”徽章 + 冒用提示）、
    `night_owl`、`new_account`。玩家列表默认排序：高危标记在前，然后按 `last_seen` 降序。
- **M2 QQ 机器人 / 白名单**（基址 `/api`）：
  - `GET /bot/status` -> `BotStatus`：`{enabled, connected, robot_name, last_error,
    groups[{openid_masked, added_at, alert_group}], pushes_today,
    maintenance{active, until}, monitor{last_online, fail_count}}`。
    `enabled=false` 时机器人未启用（旧 bot 仍在运行），`/bot` 页显示提示文案。
  - `GET /bot/applications` -> `{items: Application[]}`，`Application={name, openid_masked, ts}`。
  - `POST /bot/applications/{name}/approve|reject` -> `{reply}`。
  - `GET /bot/interactions?limit=100&q=` -> `{items: Interaction[]}`，
    `q` 匹配 `input`/`output`/`cmd`（大小写不敏感）。
    `Interaction={id, ts, group_masked, openid_masked, admin, cmd, input, output}`。
  - `POST /bot/simulate` `{content, as_admin}` -> `{cmd, reply}`：指令模拟器，不发到 QQ。
  - `GET /whitelist` -> `{items: WhitelistEntry[]}`，
    `WhitelistEntry={name, uuid, easyauth_registered, members[] /* 绑定 openid 掩码 */}`。
  - `POST /whitelist` `{name}` -> `{reply}`；`DELETE /whitelist/{name}` -> `{reply}`。
  - **reply 文本约定**：机器人风格文本原样展示，首字符编码结果——`✅` 成功 / `⚠️` 部分成功 /
    `❌` 失败（`src/lib/bot.ts` 的 `replyStatus` 据此着色）。写操作沿用 CSRF；400/409 的 `detail` 直接显示。
  - `GET /system` 的 `modules` 新增 `qq_bot` 模块（沿用模块健康展示）。
  - `openid` 一律掩码（如 `E804...5786`）；UUID 等宽字体可复制；游戏 ID 前端校验 `^[A-Za-z0-9_]{3,16}$`。
- **WebSocket** `/api/ws/console`（同源 cookie 鉴权）：服务端推送
  `log` / `event` / `status` / `command_result` / `alert`；客户端发送 `{kind:"command", command}`。
  收到 `{kind:"alert", alert}` 且 `severity==="critical"` 时右上角弹出可关闭 toast 并刷新铃铛计数。
  断线自动重连（指数退避 + 全抖动，上限 30s），顶栏显示连接状态。

类型定义集中在 `src/api/types.ts`，与此契约一一对应。

## 目录结构

```
web/src/
├─ api/        HTTP 客户端、类型、WebSocket、mock 实现、运行时选择器
├─ auth/       登录态上下文与路由守卫
├─ components/ 布局、通用 UI 原子组件（Button/Card/Input/Badge/ConfirmDialog）
├─ console/    控制台 WebSocket 上下文（全局单连接）
├─ hooks/      useTheme
├─ lib/        纯函数工具（日志着色、格式化、安全排序/铃铛/IP 可信度、机器人 reply/校验/高亮、cn）
└─ pages/      登录 / 总览 / 控制台 / 玩家(列表+详情) / IP 画像 / 安全 / QQ 机器人 / 白名单
```

## 无障碍

- 表单均有 `<label>`，错误用 `role="alert"` 公布。
- 按钮可键盘操作，确认对话框 `role="alertdialog"`、Escape 取消、焦点自动落在确认键。
- 控制台日志区 `role="log" aria-live="polite"`；告警 toast 用 `role="alert"`。
- 玩家表格行可键盘聚焦（`role="link"`，Enter/空格进入详情）；铃铛下拉用 `aria-haspopup`/`aria-expanded`。
- 亮/暗色均保证对比度，`:focus-visible` 有明显焦点环。
