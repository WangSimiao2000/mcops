#!/bin/bash
# 开发机一键部署到 mc-server：bash deploy/push.sh [--no-restart]
#
# SSH 走的是 frp 隧道，和玩家的 Minecraft 流量共用同一条 TCP 多路复用连接与云主机带宽。
# 2026-10-09 实测：不限速 scp 20+ MB 的安装包，会让在线玩家 "Timed out" 掉线（3 次掉线都与上传时刻吻合）。
# 所以这里：rsync 到固定的暂存目录（只传有变化的 wheel / 前端文件，通常 <1 MB）+ 限速。
set -euo pipefail
cd "$(dirname "$0")/.."
HOST="${MCOPS_HOST:?请用环境变量 MCOPS_HOST 指定服务器，例如 MCOPS_HOST=user@mc.example.com}"
BWLIMIT="${MCOPS_BWLIMIT:-300}"   # KiB/s，留足带宽给玩家
RESTART=1; [ "${1:-}" = "--no-restart" ] && RESTART=0

OUT="${KIROCREW_SCRATCH:-${TMPDIR:-/tmp}}/mcops-bundle"
bash deploy/make-bundle.sh "$OUT" >/dev/null
REV="$(cat "$OUT/REVISION")"
echo "== 同步 $REV（限速 ${BWLIMIT} KiB/s）"
rsync -rlt --delete --checksum --exclude=/bin/ --bwlimit="$BWLIMIT" --stats "$OUT/" "$HOST:.cache/mcops-staging/" \
  | grep -E "Number of regular files transferred|Total transferred file size"
ssh -o ConnectTimeout=10 "$HOST" bash -s -- "$REV" "$RESTART" <<'EOF'
set -euo pipefail
REV="$1"; RESTART="$2"
D="$HOME/.cache/mcops-bundles/$REV"
mkdir -p "$D" && cp -a "$HOME/.cache/mcops-staging/." "$D/"
bash "$D/deploy/install.sh" "$D" | tail -1
if [ "$RESTART" = 1 ]; then
  systemctl --user restart mcops-agent; sleep 6
  systemctl --user is-active mcops-agent
fi
EOF
