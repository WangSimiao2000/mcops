#!/bin/bash
# 在开发机生成离线安装包：bash deploy/make-bundle.sh <输出目录>
# 服务器访问 GitHub / PyPI 很慢，所以依赖在开发机按 uv.lock 的哈希下载好再带过去。
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="${1:?用法：make-bundle.sh <输出目录> [--tar]}"
TAR="${2:-}"
# 找 uv：PATH → ~/.local/bin → Kiro Crew 自带的（开发机上 agent 用的就是它）
UV="${UV:-$(command -v uv || true)}"
if [ -z "$UV" ]; then
  for c in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" $(ls -d "$HOME"/.kiro/crew-venv-*/bin/uv 2>/dev/null | sort -V | tail -1); do
    [ -x "$c" ] && { UV="$c"; break; }
  done
fi
[ -n "$UV" ] || { echo "找不到 uv（需要 0.12.x），安装方法见 https://docs.astral.sh/uv/getting-started/installation/"; exit 1; }
command -v npm >/dev/null || { echo "找不到 npm（需要 Node.js 20+）"; exit 1; }
rm -rf "$OUT" && mkdir -p "$OUT/wheels"
"$UV" export --frozen --no-dev --no-emit-project --format requirements-txt -q > "$OUT/requirements.txt"
"$UV" run --with pip==25.2 python -m pip download -q -r "$OUT/requirements.txt" \
  -d "$OUT/wheels" --only-binary=:all: --require-hashes
"$UV" build --wheel -q -o "$OUT/wheels"
(cd web && npm ci --silent && npm run -s build)
cp -r web/dist "$OUT/web-dist"
cp -r deploy "$OUT/deploy"
git describe --tags --always > "$OUT/REVISION"
mkdir -p "$OUT/bin" && install -m 755 "$UV" "$OUT/bin/uv"   # 让新服务器不用联网装 uv
if [ "$TAR" = "--tar" ]; then
  # 面板「设置 → 系统 → 升级 mc-ops」上传用；成员不带 ./ 前缀
  (cd "$OUT" && tar -czf "$OUT.tar.gz" $(ls -A))
  echo "压缩包：$OUT.tar.gz（$(du -sh "$OUT.tar.gz" | cut -f1)）"
fi
echo "安装包：$OUT（$(du -sh "$OUT" | cut -f1)，版本 $(cat "$OUT/REVISION")）"
