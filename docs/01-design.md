# mc-ops 设计文档（01-design）

> 状态：**已评审（2026-10-09）**，决策见 §8，M0 范围见 §9。
> 依据：`docs/00-inventory.md`（现有行为基线，重构的验收标准）。
> 原则：**能力一个不丢、行为可验证、随时可回退到旧系统**。

---

## 1. 目标与非目标

### 目标
1. **一个进程管全局**：服务器生命周期、崩溃自愈、定时任务、备份、升级、白名单、QQ 机器人、网络入口观测、安全审计，统一在 `mcops` 内实现，彼此通过进程内事件与数据库协作，**消灭 §3 里全部 11 条文本/文件隐式契约**。
2. **Web UI**：状态总览、实时控制台、玩家与 IP 画像、白名单审批、备份/回档、升级、定时任务、统一日志检索、安全事件。
3. **真实 IP 一等公民**：10-09 已打通 PROXY protocol，每次登录都带真实 IP，安全规则以此为基础。
4. **两台机器一个视图**：mc-server（家里）+ frps 云主机（腾讯云）的日志与状态汇总在一处。
5. **本地可完整开发与测试**：笔记本上起一个真实的小型 Fabric 服务端 + 假 QQ 适配器，端到端跑通后再部署。

### 非目标
- 不做多租户 / 多服务器托管面板（只服务这一台服）。
- 不对公网开放 Web UI。
- 不替换游戏侧 Mod 配置管理（LuckPerms、EasyAuth 等配置仍手工维护，平台只读展示 + 少量受控开关）。

---

## 2. 总体架构

```
 笔记本浏览器 ──ssh -L 8765──┐
                             ▼
┌──────────────────────── mc-server (家里) ──────────────────────────┐
│ mcops-agent （单进程 asyncio，systemd 托管，监听 127.0.0.1:8765）    │
│                                                                    │
│  api/        FastAPI：REST + WebSocket（控制台/日志/事件推送）       │
│  core/       事件总线 · SQLite（WAL）· 配置 · 密钥 · 审计            │
│  server/     MC 进程托管 · RCON 命令通道 · 就绪/健康判定 · watchdog   │
│  logs/       latest.log 实时解析 → 结构化事件（登录/IP/认证/聊天/异常）│
│  players/    玩家·会话·IP 画像 · 白名单（离线 UUID）· 申请审批 · 封禁  │
│  backup/     冷/热备份 · 保留策略 · 回档 · （可选）restic 去重         │
│  upgrade/    MC+Fabric+Mod 升级 · 快照 · 自动回滚                     │
│  scheduler/  定时任务（替代 crontab）：每日重启、清理、更新检查        │
│  bot/        QQ 适配器（botpy）· 指令路由 · 主动推送配额               │
│  security/   规则引擎：异常时段/新号/新 IP/爆破/外挂特征 → 告警/处置   │
│  edge/       云主机采集：经 SSH 拉取 frps journald（只读受限密钥）     │
│  notify/     通知通道：QQ 管理员 / 邮件 / Web 铃铛                    │
│                                                                    │
│  ├─ java (Fabric 1.21.11) ── 由 agent 以子服务方式托管               │
│  └─ frpc ──(10.88.88.1 PROXY v2)── MC 25565 / sshd 22               │
└────────────────────────────────────────────────────────────────────┘
          ▲  只读 SSH（forced-command，journalctl -u frps -f -o json）
          │
   frps 云主机 203.0.113.10（不装 agent，零新增端口）
```

`web/` 是独立的前端工程，构建产物由 agent 以静态文件形式在同一端口提供。

### 2.1 为什么是单进程
- 现有痛点是"各管各的 + 文本契约"。单进程内模块直接调用函数、订阅事件，契约变成**有类型的 Python 接口**，可以被 mypy 与测试守护。
- 机器人、看门狗、调度器都是轻量 IO 任务，asyncio 足够；每个模块是**受监督的 task**，单个模块崩溃会被记录并按退避重启，不拖垮整体。
- MC 本体**不在** agent 进程内（见 §3.1），agent 重启/升级不会让游戏掉线。

---

## 3. 关键设计决策

每条给出选择、理由与替代方案，标 **[待确认]** 的需要你拍板。

### 3.1 MC 进程托管：systemd 单元 + RCON（替代 tmux send-keys）**[待确认]**
- **选择**：MC 由独立的 `mc-server.service`（`Restart=no`，由 agent 决定是否拉起）运行；命令通道改为 **RCON（仅 127.0.0.1，随机强密码）**；控制台输出靠 agent 实时 tail `latest.log`。
- **理由**：
  - RCON 命令**有返回值**（如 `whitelist list` 直接拿到结果），不必再"发命令→等日志→正则回读"，旧系统里最脆弱的一类逻辑（`_reload_and_verify`、`mc.sh` stdout 解析）整体消失。
  - agent 重启/升级不影响游戏进程；systemd 负责进程生命周期与资源限制（`MemoryMax`、`Nice`），journald 留存 stdout。
- **代价**：失去 `tmux attach` 的原生控制台。用 Web 控制台 + `mcops console`（终端 REPL，走同一 RCON 通道）替代。
- **安全**：MC 的 RCON 无法单独绑定地址（跟随 `server-ip`），因此依赖 mc-server 本机 ufw 默认 DROP（已启用）挡住局域网；frpc 不转发 25575。
- **替代方案**：保留 tmux。实现最省事，但旧的文本契约问题照旧。

### 3.2 数据：SQLite 作为单一真相源
- 表（节选）：`events`（所有结构化事件，带类型/来源/时间/JSON 载荷）、`players`、`player_sessions`（登录/登出/IP/认证方式/时长）、`ip_intel`（首次/末次出现、关联账号数、地理/ASN 缓存）、`whitelist_applications`、`group_bindings`、`backups`、`upgrade_snapshots`、`jobs` / `job_runs`、`alerts`、`audit_log`（Web 与 QQ 的每一次管理操作）。
- `whitelist.json` / `banned-*.json` 仍由 MC 读，**agent 是唯一写入方**（统一锁），写后用 RCON `whitelist reload` 生效并核对。
- 迁移导入：旧 `applications.json`、`members.json`、`groups.json`、`whitelist-audit.log`、`interactions.log` 一次性导入；历史 `latest.log*.gz` 回填为会话与 IP 事件（10-08 之前的 IP 都是 127.0.0.1，标记为"不可信 IP"）。

### 3.3 QQ 机器人并入 agent
- botpy 作为 `bot/` 模块内的一个受监督 task；指令处理直接调用 `players/`、`server/` 的接口，不再 shell-out。
- 保留全部现有指令、权限模型（fail-closed、信息暴露面收口、申请防刷）与文案；`test_exposure.py` 的断言原样移植为回归测试。
- 新增：**主动推送配额计量**（每日额度、按优先级取舍，安全告警优先于播报）。
- 开发环境用 `FakeQQAdapter`（本地 CLI/Web 模拟群消息），无需真实 appid。

### 3.4 云主机 frps：SSH 拉取，不装 agent **[待确认]**
- 在云主机给 `ubuntu` 之外新建 `mcops-ro` 用户，`authorized_keys` 里用 `command="journalctl -u frps -f -o json --since ..."`、`restrict` 限死成只读拉日志；mc-server 侧 agent 持专用密钥常驻拉取。
- **理由**：云主机零新增端口、零常驻代码；密钥泄露也只能读 frps 日志。
- 拿到的数据：frps 每条 user connection 的真实 IP 与代理名（`ssh`/`minecraft`）、frpc 登录（含被拒绝的 token 尝试，如 censys）。
- **可选的处置能力**（默认关闭）：若要"自动在云主机封 IP"，需要另一个受限命令（只允许 `ipset add mcops-ban <ip>`）。先只做观测。

### 3.5 安全规则引擎（security/）
首批规则，全部来自这次事件的经验：

| 规则 | 依据 | 默认动作 |
|---|---|---|
| 凌晨 01:00–06:00 登录 | 10-04 02:47 作案 | 告警（QQ 管理员 + 邮件） |
| 已知账号出现**新 IP / 新 ASN / 新国家** | 冒用 `Yuki_Y` | 告警；可选：要求重新输密码（EasyAuth 本来就每次要） |
| 新账号注册 | `Scout42` 踩点 | 告警 |
| 单 IP 短时多账号 / 多次 EasyAuth 失败 | 密码爆破 | 告警 + 自动 `ban-ip`（可配置时长） |
| `moved too quickly` / `floating too long` / 飞行被踢 集中出现 | 外挂特征 | 告警 |
| 客户端指纹突变（如原本有 carpet 握手，突然没有） | 复盘里证明身份冒用的关键证据 | 告警 |
| frps 上 `ssh` 代理单 IP 连接激增 | 爆破 | 告警；（可选）云主机 ipset 封禁 |
| 白名单内但 EasyAuth 未注册的账号 | 10-09 发现 `Zoe`、`EveX` | 面板常驻红色提示 |

每条规则可在 UI 中开关、调阈值、加白（如自己的 IP）。

### 3.6 备份：保留 tar 冷备，新增 restic 去重 **[待确认]**
- 现状 `backups/` 已 22 GB，`pre-upgrade-*` 只增不减。
- **选择**：保留现有 tar 冷备语义（`.fabric` 必备份、`save-off/flush`、min_keep 保护）作为"可人工直接解压"的兜底；新增 restic 仓库做每日/每小时去重增量，可选同步到云主机或对象存储。
- 统一保留策略：tar 冷备与升级快照也纳入 GFS（日/周/月）清理。
- 回档改为 Web 上"选择时间点 → 预览差异（世界大小、玩家数据、配置）→ 自动先备份当前 → 执行"。

### 3.7 定时任务：内置调度器替代 crontab
- 每日 05:00 重启 + 冷备、清理、更新检查、IP 情报刷新等都由 `scheduler/` 执行，每次运行记录到 `job_runs`（开始/结束/结果/日志），UI 可手动触发、暂停、查看历史。
- watchdog 不再是"每分钟轮询"，而是 systemd 进程退出事件 + 健康探测（SLP ping 与 RCON 心跳），崩溃判定沿用旧阈值（窗口 10 分钟内 3 次 → 停止自愈并告警）。

### 3.8 Web UI 与访问控制
- **只监听 127.0.0.1**；访问方式：`ssh -L 8765:127.0.0.1:8765 mc-server`，或以后改用 frp `stcp`。**不经 frp 暴露。**
- 即使只在本机，仍做完整鉴权：单管理员账号，argon2 密码 + TOTP；HttpOnly + SameSite=Strict 会话；所有写操作带 CSRF 校验并写入 `audit_log`。
- 危险操作（回档、升级、任意 RCON 命令、封禁 IP）需二次确认，任意 RCON 命令在 UI 上与普通操作分区。
- 页面：总览 · 控制台 · 玩家（在线/历史/会话/IP 画像/白名单/申请审批/封禁）· 安全（事件流/规则/frps 连接）· 备份与回档 · Mod 与升级 · 定时任务 · 日志检索 · 网络（frpc/frps 状态）· 设置。

### 3.9 配置与密钥
- `mcops.yaml`（非机密，可入库示例 `mcops.example.yaml`）+ `secrets.yaml`（`chmod 600`，不入库）：QQ appid/secret、SMTP 授权码、RCON 密码、Web 管理员密码哈希、edge 只读密钥路径。
- 所有路径来自配置，代码中不再出现 `/home/steve/...`。

---

## 4. 技术栈

| 层 | 选择 | 理由 |
|---|---|---|
| 语言/运行时 | Python 3.12，uv 管理依赖 | 与现有 bot 一致；uv 已装 |
| Web 框架 | FastAPI + uvicorn | 异步、类型化、自带 OpenAPI，前端可生成客户端 |
| 数据库 | SQLite（WAL）+ SQLAlchemy 2 + Alembic | 单机足够；迁移可控 |
| QQ | qq-botpy（沿用） | 已验证 |
| MC 通信 | RCON（自写极小客户端）+ SLP ping（沿用 `mc_ping.py` 逻辑） | 无重依赖 |
| 前端 | Vite + React + TypeScript + Tailwind + shadcn/ui；TanStack Query；xterm.js（控制台）；ECharts（图表） | 交互多、定制度高 |
| 测试 | pytest + pytest-asyncio；Playwright（UI 冒烟）；本地真实 Fabric 服务端做 E2E | 见 §6 |
| 质量 | ruff + mypy（strict）+ pre-commit；GitHub Actions | |
| 部署 | systemd：`mcops-agent.service`、`mc-server.service` | 与现状一致 |

---

## 5. 迁移与回退

旧系统（`~/minecraft` 脚本 + crontab + `mc-qq-bot.service`）在新系统**完全接管并稳定运行 2 周前不删除**。

| 阶段 | 新系统做什么 | 旧系统状态 | 回退方式 |
|---|---|---|---|
| M0 本地开发 | 笔记本上对本地测试服务端全功能开发 | 不受影响 | — |
| M1 旁路观测 | 生产部署 agent，**只读**：解析日志、拉 frps、安全告警、Web 只读面板 | 照常运行，全权负责 | 停 agent |
| M2 接管 QQ | 停旧 bot，新 bot 上线（同一 appid） | 旧 bot 停用但保留 | `systemctl --user start mc-qq-bot` |
| M3 接管控制 | MC 改由 `mc-server.service` + RCON 托管；调度器接管重启/备份；注释掉 crontab | 脚本保留不用 | 恢复 crontab，`mc.sh start` |
| M4 增强 | restic、安全自动处置、frps ipset、UI 打磨 | — | 关开关 |
| M5 退役 | 旧仓库归档，README 指向 mc-ops | 归档 | — |

每个阶段结束都有对照验收：以 `00-inventory.md` 为清单逐项打勾。

---

## 6. 本地开发与测试

- `dev/` 目录提供一键脚本：下载 Fabric 1.21.11 服务端 + 关键 Mod（EasyAuth、proxy-protocol-support、carpet），生成离线小世界，跑在笔记本上（OpenJDK 21，2 GB 堆）。
- `FakeQQAdapter`：本地模拟群聊，用于开发与 E2E。
- 可选：本地起一对 frps/frpc，验证 PROXY protocol 与 IP 采集链路。
- 测试分层：
  1. 单元测试：解析器（日志行 → 事件）、离线 UUID、保留策略、规则引擎、权限。
  2. 契约测试：把 `00-inventory.md` 里的每条能力写成测试，包括旧 bash 测试覆盖的边界（锁、就绪判定、min_keep、`.fabric` 必备份等）。
  3. 端到端：对本地真实服务端跑"启动→进服（模拟客户端）→白名单申请→批准→备份→回档→升级回滚"。
  4. 回放测试：用生产的 `latest.log` 历史（脱敏）回放，检验解析器与安全规则，**必须能复现 10-04 事件的告警**。

---

## 7. 仓库结构（计划）

```
mc-ops/
├─ src/mcops/
│  ├─ core/  api/  server/  logs/  players/  backup/  upgrade/
│  ├─ scheduler/  bot/  security/  edge/  notify/
│  └─ cli.py                # mcops console / status / backup ... 运维命令行
├─ web/                     # 前端
├─ migrations/              # Alembic
├─ tests/  { unit, contract, e2e, replay }
├─ dev/                     # 本地测试服务端、假 QQ、fixtures
├─ deploy/                  # systemd 单元、安装/迁移/回退脚本
├─ docs/                    # 00-inventory、01-design、运维手册、ADR
├─ mcops.example.yaml
└─ pyproject.toml
```

---

## 8. 已确认的决策（2026-10-09）

1. **§3.1** MC 改为 `mc-server.service` + RCON 托管，放弃 tmux；控制台由 Web + `mcops console` 提供。
2. **§3.4** 云主机 frps 日志经受限只读 SSH（forced-command）拉取，云主机不装常驻程序、不开新端口。
3. **§3.6** 引入 restic 去重增量备份，tar 冷备保留作兜底。异地副本推迟到 M4 再选（云主机磁盘 / COS），M0–M3 只做本机 restic 仓库。
4. **告警渠道**：两者都要，按级别分发——`critical`（疑似冒用、爆破、反复崩溃）发 QQ 管理员 + 邮件；`warning`（凌晨登录、新号注册等）只发邮件 + Web 铃铛，节省 QQ 主动消息额度。

另：仓库主干为 `dev`（agent 直接推送），`main` 仅在发布时由 OpAdmin 同步。

（已确认：笔记本 OpenJDK 21.0.12、16 核 / 15 GB 内存、无 Docker，本地测试服务端直接用 Java 运行，分配 2 GB 堆即可。）

---

## 9. M0 范围（本地开发基线）

M0 结束时，笔记本上应能做到：

1. `uv sync && uv run mcops-agent --config dev/mcops.dev.yaml` 启动 agent，`/api/health` 返回各模块状态。
2. `dev/server/` 里一键拉起真实的 Fabric 1.21.11 测试服（EasyAuth、proxy-protocol-support、carpet，离线、白名单、RCON 仅本机）。
3. agent 能托管该测试服的启停（开发环境由 agent 直接拉起子进程，生产用 systemd，接口一致），通过 RCON 发命令并拿到返回，实时解析 `latest.log` 为结构化事件写入 SQLite。
4. Web 前端骨架（登录页 + 总览 + 实时控制台）能连上 agent。
5. CI：ruff、mypy --strict、pytest 全绿；包含日志解析器对生产日志样本（脱敏）的回放测试。

不在 M0：QQ、备份、升级、安全规则、frps 采集（这些从 M1 起按模块逐个实现）。

---

## 10. 待办（用户提出，排期待定）

- **面板内配置通知渠道**（2026-10-09）：在 Web「设置 → 通知」页里增删邮件告警（SMTP 服务器、端口、发件人、收件人、按严重度订阅），带「发送测试邮件」按钮，保存后即时生效，不需要登录服务器改 `mcops.yaml` / `secrets.yaml`。
  - SMTP 授权码不回显（只显示"已设置"），存储时用服务器本地密钥文件（`~/.config/mcops/settings.key`，600）加密后写入数据库；修改记入审计日志，需要二次确认。
  - 同一套"通知渠道"模型后续扩展 QQ 管理员、Bark / Server 酱 / Webhook。
  - 配置文件里的 `smtp:` 段保留为可选的初始值（面板保存后以数据库为准）。
