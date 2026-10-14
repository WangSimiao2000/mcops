# 参与贡献

欢迎提 issue 和 pull request。

## 提 issue

- **Bug**：写清楚 mcops 版本、系统、Minecraft / Fabric 版本，以及复现步骤。附上 `journalctl --user -u mcops-agent` 里的相关日志时，请先删掉 IP、玩家名等个人信息。
- **功能建议**：说明你想解决什么问题。mcops 只管一台 Fabric 服务器，多服务器托管和 Paper / Forge 支持暂不在计划内。
- **安全问题**：不要发公开 issue，见 [SECURITY.md](SECURITY.md)。

## 提交代码

1. 按 README「开发」一节搭好本地环境。本地会拉起一个真实的 Fabric 测试服，需要 Java 21。
2. 改动尽量小而集中，一个 PR 解决一件事。
3. 新功能和 bug 修复请带测试：后端用 pytest（`tests/`），前端用 Vitest（`web/src/**/*.test.ts(x)`）。
4. 提交前跑一遍 `bash dev/check.sh`，它执行的检查和 CI 完全一样：ruff format / ruff check、mypy --strict、pytest、前端 typecheck / lint / test / build。
5. 测试数据里不要出现真实的玩家名、IP、主机名或 QQ openid。IP 请用文档专用地址段（`192.0.2.0/24`、`198.51.100.0/24`、`203.0.113.0/24`）；需要公网地址时可以用 `93.184.216.0/24`。

### 代码约定

- 后端：Python 3.12，asyncio；配置用 pydantic 模型（`src/mcops/config.py`），新配置项要在 `configstore.py` 里给出中文标题；面向用户的文字用中文。
- 前端：React + TypeScript + Tailwind；颜色同时考虑亮色和暗色主题；交互元素要能用键盘操作。
- 会改动游戏目录或停服的操作，必须走任务系统（`JobRunner`），并写审计日志。

提交 PR 即表示你同意以 AGPL-3.0 许可证发布你的贡献。
