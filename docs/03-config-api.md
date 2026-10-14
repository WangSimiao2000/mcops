# 03 · 面板配置接口（P1：配置入库 + 面板编辑）

## 分层

- `mcops.yaml`：**默认值** + 启动必需项（`data_dir`、`secrets_file`、`api.host/port/static_dir`、`mode`），后者面板只读。
- 数据库覆盖层（kv `config.overlay`）：面板改过的字段。生效配置 = yaml 深合并覆盖层，再整体用 `Config` 校验。
- 面板改过的字段以面板为准；可按字段「恢复为 yaml 默认」。
- 每次保存写一条 `config_history`（谁、何时、改了哪些路径、保存后的覆盖层），可一键恢复。

## 生效方式（schema 扩展字段 `x-apply`）

| 值 | 含义 | 面板提示 |
|---|---|---|
| `agent` | 重启 agent 后生效（不影响在线玩家） | 顶部横幅「有 N 项改动待应用」+「立即应用（重启 agent）」 |
| `mc` | 下次 MC 开服生效 | 「下次开服生效」（05:00 定时重启或手动重启） |
| `live` | 立即生效 | 无 |

## 接口

所有接口需登录；写操作需 CSRF。路径分隔用 `.`，如 `server.java_opts`、`ops.restic.interval_minutes`。

### GET /api/config
```json
{
  "schema": { "...JSON Schema（只含可编辑部分）": "" },
  "sections": [{"key": "server", "title": "服务端", "description": "..."}],
  "values": { "server": {"java_opts": ["-Xms8G","-Xmx8G"], "...": ""}, "ops": {}, "...": "" },
  "overridden": ["server.java_opts", "ops.restart_time"],
  "pending": [{"path": "server.java_opts", "apply": "agent"}],
  "readonly": {"mode": "control", "data_dir": "...", "api": "127.0.0.1:8765", "config_file": "..."}
}
```
- schema 是 pydantic `model_json_schema()` 产物（`$defs` + `$ref`），字段带 `title`、`description`、`x-apply`，可选 `x-advanced: true`（默认折叠）、`x-unit`（如 `"分钟"`）、`x-widget`（`"memory"` 内存滑块、`"time"` HH:MM、`"lines"` 字符串数组按行编辑）。
- `pending`：已保存但运行中的 agent 尚未采用的改动（`agent` 类直到 agent 重启；`mc` 类直到下次开服）。

### PUT /api/config
请求：`{"values": {...与 GET values 同结构，可只给部分 section...}, "note": "可选备注"}`
- 只把与当前生效值不同的叶子写进覆盖层。
- 200：`{"ok": true, "changed": [{"path": "...", "apply": "agent"}], "pending": [...], "values": {...}, "overridden": [...]}`
- 422：`{"detail": "server.port：必须在 1-65535 之间"}`

### POST /api/config/reset
`{"paths": ["server.java_opts"]}` → 删除这些覆盖，回到 yaml 值。返回同 PUT。

### GET /api/config/history?limit=50
`{"items": [{"id": 3, "ts": "ISO", "user": "admin", "changed": ["server.port"], "note": ""}]}`

### POST /api/config/history/{id}/restore
把覆盖层恢复为该次保存后的状态。返回同 PUT。

### POST /api/system/restart-agent
202 `{"accepted": true}`：交给 systemd 重启 agent（`systemd-run --user --on-active=2 systemctl --user restart <unit>`），MC 不受影响，前端轮询 `/api/health` 恢复后刷新。
409：不在 systemd 下运行（开发模式），提示手动重启。

### GET /api/capabilities
```json
{
  "mc_version": "1.21.11", "loader": "fabric", "loader_version": "0.19.2",
  "mods": {"easyauth": true, "carpet": true, "proxy_protocol": true, "minimotd": true},
  "proxy_source": "10.88.88.1",
  "features": {"edge": true, "bot": true, "restic": true, "systemd": true, "flight_watch": true}
}
```
前端据此隐藏无关页面/区块（无 bot → 隐藏 QQ 机器人页；无 edge → 网络页隐藏 frps 部分；无 easyauth → 玩家页不显示「未注册」列）。

## server.properties

### GET /api/gamecfg/properties
```json
{
  "path": ".../server.properties",
  "items": [
    {"key": "difficulty", "value": "normal", "known": true, "type": "enum",
     "options": ["peaceful","easy","normal","hard"], "group": "玩法",
     "title": "难度", "description": "...", "managed": false}
  ],
  "pending": ["view-distance"]
}
```
- `type`：`bool` / `int`（带 `min`/`max`）/ `enum`（`options`）/ `string`。
- `managed: true`：由 mc-ops 管理（`enable-rcon`、`rcon.port`、`rcon.password`、`server-port`、`motd` 若配置了 MOTD 同步），只读并说明原因；`rcon.password` 不返回值（`value: null`, `sensitive: true`）。
- 未知键 `known: false`，按字符串在「高级」里编辑。
- `group`：玩法 / 世界 / 玩家与权限 / 网络与性能 / 其它。

### PUT /api/gamecfg/properties
`{"changes": {"difficulty": "hard", "view-distance": 12}}` → 200 `{"ok": true, "changed": ["difficulty","view-distance"], "backup": "server.properties.bak-20261010-140000"}`；改动在下次开服生效。
422：类型/范围不对或改了 managed 键。

## 账号与机密

- `POST /api/auth/password` `{"old_password","new_password"}`（新密码 ≥12 位）→ 204；吊销所有会话（含当前），前端跳登录页。
- `GET /api/auth/totp` → `{"enabled": bool}`
- `POST /api/auth/totp/setup` → `{"secret": "BASE32", "otpauth_uri": "otpauth://totp/mc-ops:admin?secret=...&issuer=mc-ops"}`（未确认前不生效）
- `POST /api/auth/totp/confirm` `{"code": "123456"}` → `{"enabled": true}`；422 验证码不对
- `POST /api/auth/totp/disable` `{"password": "..."}` → `{"enabled": false}`
- `GET /api/secrets` → `{"items": [{"key": "qq_appid", "title": "QQ 机器人 AppID", "set": true, "editable": true, "apply": "agent"}, {"key": "restic_password", "set": true, "editable": false, "note": "改密需 restic key passwd，面板不支持"}]}`
- `PUT /api/secrets` `{"qq_appid": "...", "qq_secret": "..."}`（只写；空字符串 = 不改）→ `{"ok": true, "pending": [...]}`
