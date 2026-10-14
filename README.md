# mcops

自托管 Minecraft（Fabric）服务器的一体化运维平台：一个后台进程加一个 Web 面板，把开服停服、定时重启、备份与回退、版本和 Mod 升级、玩家与安全审计、QQ 群机器人收拢到一处。

> 这个项目最初是为作者自己的服务器写的，已在一台 40 多个 Mod、十几位玩家的 Fabric 1.21 服务器上稳定运行。设计取舍和踩坑记录见[这组博客文章](https://blog.mickeymiao.cn/posts/MC-Ops-Platform/)。

English: mcops is a self-hosted operations platform for a Fabric Minecraft server — a single agent process plus a web panel for lifecycle, scheduled restarts, backups and point-in-time restore (restic), version/mod upgrades with rollback, player/IP auditing with security alerts, and an optional QQ group bot. The UI and docs are in Chinese.

## 功能

- **服务器托管**：MC 跑在独立的 `systemd --user` 单元里，由 mcops 决定启停；命令走 RCON（有返回值）。mcops 自身重启、升级都不会让玩家掉线。崩溃自愈，反复崩溃时停止重启并告警。
- **首次安装向导**：在空目录里选版本、内存、端口、正版或离线，自动下载 Fabric 服务端（Java 版本不够时下载 Temurin JRE）、生成配置并开服。
- **Web 面板**：总览（服务器状态、真人和假人分开显示的在线玩家、主机 CPU / 内存 / 磁盘 / 网络和 MC 进程资源占用）、实时控制台、日志检索、运维任务历史和实时进度。
- **备份**：每日定时重启时打 tar.gz 冷备份；可选 [restic](https://restic.net/) 增量快照（拍快照前 `save-off` + `save-all flush` 保证一致），并自动复制到局域网 NAS（SMB，经 rclone）或 S3 兼容存储；面板一键回退到任意快照，回退前自动拍 `pre-restore` 快照，开服失败自动换回。
- **升级**：检查目标版本下每个 Mod 在 Modrinth 上是否有兼容版本、是否需要换 Java；预先下载依赖；失败自动回滚。Mod 的安装、更新、停用也在面板里完成。
- **玩家与安全**：会话、IP 和网段画像，白名单与封禁管理；十几条可调阈值的安全规则（凌晨登录、账号出现在新网段、密码爆破、冒用玩家名进服、外挂特征、SSH 高频连接……），结合鞘翅飞行状态过滤 `moved too quickly` 误报；告警推送到面板、邮件或 QQ 群。
- **QQ 机器人**（可选）：基于 QQ 官方机器人，查询状态（TPS / MSPT、frp 隧道延迟）、在线玩家、Mod 列表，申请白名单、管理员审批；获批后限期未注册自动移出。
- **frp 部署友好**（可选）：通过受限只读 SSH 拉取云主机上 frps 的日志，拿到每条外部连接的真实 IP；配合 PROXY protocol 让服务端看到玩家真实 IP。
- **配置**：几乎所有设置都能在面板里改，带修改历史，大多即时生效；16 项自检给出修复建议；面板内上传安装包升级 mcops 本身，可一键回滚。

## 环境要求

- Linux x86_64，带 systemd（开发和生产都在 Ubuntu 24.04 上）
- Python 3.12 或更新（Ubuntu 24.04 自带）；`uv` 随安装包附带，无需联网安装
- 一次 `sudo loginctl enable-linger <用户>`，让用户级服务在退出登录后继续运行
- 内存：Minecraft 本身的需求之外，mcops 约占 100 MB
- 可选：`restic`（增量快照）、`rclone`（NAS 异地副本）

mcops 只托管 **Fabric** 服务端；Paper / Forge 等目前不支持。

## 快速开始

在服务器上以**普通用户**执行（不需要 root）：

```bash
# 1. 从 Releases 下载安装包并解压
mkdir mcops-bundle
tar -xzf mcops-<版本>.tar.gz -C mcops-bundle

# 2. 安装（幂等，升级也用这条；已有的配置、机密、数据库不会被覆盖）
bash mcops-bundle/deploy/install.sh mcops-bundle

# 3. 允许用户服务常驻，然后启动
sudo loginctl enable-linger "$USER"
systemctl --user enable --now mcops-agent
```

安装后的位置：程序在 `~/mc-ops`，配置和机密在 `~/.config/mcops/`，数据库在 `~/.local/share/mcops/`，默认游戏目录是 `~/minecraft/server`。

**打开面板。** 面板只监听 `127.0.0.1:8765`，不对外开放。在你自己的电脑上建一条 SSH 隧道：

```bash
ssh -L 8765:127.0.0.1:8765 <用户>@<服务器>
```

然后浏览器打开 <http://127.0.0.1:8765>。初始密码在服务器的 `~/.config/mcops/admin-password.txt`，登录后请在「设置 → 账号与安全」里修改，并删掉这个文件（自检会提醒）。

**安装 Minecraft。** 游戏目录为空时，总览页会显示安装向导，按提示选择版本并同意 EULA 即可。

### 接管已有的服务器

把 `~/.config/mcops/mcops.yaml` 里的 `server.game_dir` 指向现有游戏目录，`server.jar` 填服务端 jar 的文件名（支持通配符），然后重启 agent。

想先试试、不碰正在运行的服务器，可以把 `mode` 设为 `observe`：mcops 只读日志、做审计和告警，不会启停服务器。确认没问题后改回 `control`，由 mcops 生成并托管 `mc-server.service`。

mcops 需要 RCON 来发命令：在 `server.properties` 里开启 `enable-rcon`，并把 `rcon.password` 写进 `~/.config/mcops/secrets.yaml` 的 `rcon_password`。用安装向导装的服务器会自动配好。**不要把 RCON 端口暴露到公网。**

## 可选功能

| 功能 | 怎么开启 |
|---|---|
| restic 增量快照 | 安装 restic，在「设置 → 服务器配置 → 运维任务 → 增量快照」开启；仓库密码写进 `secrets.yaml` 的 `restic_password`。**请把这个密码另外保存一份**，丢了备份就解不开了 |
| 异地副本 | 同一位置的「异地副本」：填 NAS 的 SMB 地址和共享名（需要 rclone），或 S3 兼容存储的地址；账号密码在「账号与安全 → 机密」里填 |
| 邮件告警 | 「设置 → 通知」填 SMTP，可发送测试邮件 |
| QQ 机器人 | 在 [QQ 开放平台](https://q.qq.com/) 创建机器人，把 appid / secret 填进机密，在「QQ 机器人」设置里开启并填写群和管理员 |
| frps 日志采集 | 在云主机上运行 `deploy/edge/install-on-frps-host.sh` 创建只能读 frps 日志的受限账号，再在 `mcops.yaml` 里配置 `edge`（示例见 `deploy/mcops.example.yaml` 的注释） |

## 安全模型

- 面板只监听回环地址（配置校验强制），通过 SSH 隧道或 WireGuard 访问。
- argon2 密码 + 可选 TOTP；数据库会话（HttpOnly、SameSite=Strict）；写操作 CSRF 校验；Host / Origin 校验防 DNS rebinding；登录限流；所有管理操作记审计日志。
- 机密放在 `secrets.yaml`（权限必须是 600，否则拒绝启动），在面板里填的 SMTP 授权码存在本机数据库里（安装时目录权限为 700）；面板对机密只写不读，不会回显，也不写进日志。
- 生成的 `mc-server.service` 带不建命名空间的 systemd 加固（`NoNewPrivileges`、`RestrictNamespaces` 等）。

发现安全问题请看 [SECURITY.md](SECURITY.md)。

## 开发

需要 Python 3.12 + [uv](https://docs.astral.sh/uv/)、Node.js 20+（CI 用 24）、Java 21。

```bash
uv sync
uv run python dev/setup_server.py      # 准备本地 Fabric 测试服（127.0.0.1:25566）和开发用机密
(cd web && npm ci && npm run build)    # 构建前端，由 agent 在同一端口提供
uv run mcops-agent --config dev/mcops.dev.yaml
```

浏览器打开 <http://127.0.0.1:8766>，密码在 `dev/.local/admin-password.txt`。前端热更新：另开终端 `cd web && npm run dev`，访问 <http://127.0.0.1:5173>。

```bash
bash dev/check.sh                      # 与 CI 相同的全部检查（ruff、mypy、pytest、前端 typecheck/lint/test/build）
bash deploy/make-bundle.sh out --tar   # 生成离线安装包 out.tar.gz
```

设计文档在 `docs/`：[01-design.md](docs/01-design.md)（架构与关键决策）、[03-config-api.md](docs/03-config-api.md)（配置与 API）。贡献方式见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

[AGPL-3.0](LICENSE)。如果你修改了 mcops 并通过网络向他人提供服务，需要向这些用户提供修改后的源代码；面板侧栏底部的「源代码」链接就是为此准备的，请改成你自己的仓库地址（`web/src/components/AppLayout.tsx` 里的 `SOURCE_URL`）。
