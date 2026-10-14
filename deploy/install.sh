#!/bin/bash
# 在 mc-server 上以普通用户运行（不需要 sudo）：从离线安装包部署 / 升级 mc-ops agent。
#   bash deploy/install.sh <bundle 目录>
# 安装包由 deploy/make-bundle.sh 在开发机生成（依赖按哈希锁定的 wheel + 前端产物）。
# 幂等：已有的配置、机密、密钥、数据库不会被覆盖。
set -euo pipefail
BUNDLE="$(cd "${1:?用法：install.sh <bundle 目录>}" && pwd)"
APP="$HOME/mc-ops"
CONF="$HOME/.config/mcops"
DATA="$HOME/.local/share/mcops"
UV="$HOME/.local/bin/uv"
umask 077

mkdir -p "$APP" "$CONF" "$DATA" "$HOME/.config/systemd/user" "$HOME/.local/bin"
# 安装包自带 uv（与开发机同架构），服务器上没有就装到 ~/.local/bin
if [ ! -x "$UV" ] && [ -x "$BUNDLE/bin/uv" ]; then
  install -m 755 "$BUNDLE/bin/uv" "$UV"
fi
[ -x "$UV" ] || { echo "缺少 uv：安装包里没有，也不在 $UV"; exit 1; }
PY=""
for c in python3.13 python3.12; do
  if command -v "$c" >/dev/null; then PY="$(command -v "$c")"; break; fi
done
[ -n "$PY" ] || { echo "需要 Python 3.12 或更新版本（Ubuntu 24.04 自带 python3.12）"; exit 1; }

echo "== 安装依赖（离线、校验哈希）"
[ -d "$APP/.venv" ] || "$UV" venv -q --python "$PY" "$APP/.venv"
"$UV" pip install -q --python "$APP/.venv/bin/python" --offline --no-index \
  --find-links "$BUNDLE/wheels" --require-hashes -r "$BUNDLE/requirements.txt"
"$UV" pip install -q --python "$APP/.venv/bin/python" --offline --no-index --no-deps \
  --no-cache --reinstall-package mcops "$BUNDLE"/wheels/mcops-*.whl

echo "== 前端与部署文件"
rm -rf "$APP/web.new" && mkdir -p "$APP/web.new" && cp -r "$BUNDLE/web-dist" "$APP/web.new/dist"
rm -rf "$APP/web" && mv "$APP/web.new" "$APP/web"
rm -rf "$APP/deploy" && cp -r "$BUNDLE/deploy" "$APP/deploy"
cp "$BUNDLE/REVISION" "$APP/REVISION"

echo "== 配置（已存在则保留）"
FRESH=0
if [ ! -f "$CONF/mcops.yaml" ]; then
  FRESH=1
  sed "s#__HOME__#$HOME#g" "$BUNDLE/deploy/mcops.example.yaml" > "$CONF/mcops.yaml"
  mkdir -p "$HOME/minecraft/server"
fi
if [ ! -f "$CONF/secrets.yaml" ]; then
  PW="$("$APP/.venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(18))')"
  HASH="$(printf '%s' "$PW" | "$APP/.venv/bin/python" -c 'import sys; from mcops.auth import hash_password; print(hash_password(sys.stdin.read()))')"
  printf 'admin_password_hash: "%s"\n# smtp_password: "<邮箱 SMTP 授权码>"\n' "$HASH" > "$CONF/secrets.yaml"
  printf '%s\n' "$PW" > "$CONF/admin-password.txt"
  echo "   已生成面板密码：$CONF/admin-password.txt（首次登录后建议用 mcops hash-password 更换）"
fi
chmod 600 "$CONF"/*

echo "== systemd --user 单元"
install -m 644 "$BUNDLE/deploy/systemd/mcops-agent.service" "$HOME/.config/systemd/user/mcops-agent.service"
systemctl --user daemon-reload
if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]; then
  echo "!! 还没开启 linger：退出 SSH 后服务会停。请执行一次：sudo loginctl enable-linger $USER"
fi
echo "完成。版本 $(cat "$APP/REVISION")。启动：systemctl --user enable --now mcops-agent"
if [ "$FRESH" = 1 ]; then
  echo "首次安装：面板 http://127.0.0.1:8765（经 ssh -L 8765:127.0.0.1:8765 访问），"
  echo "          密码在 $CONF/admin-password.txt；登录后总览页会引导你安装 Minecraft 服务端。"
fi
