#!/usr/bin/env bash
# 本地跑一遍与 .github/workflows/ci.yml 完全相同的检查。推送前执行：bash dev/check.sh
# 只想查后端：bash dev/check.sh backend；只查前端：bash dev/check.sh web
set -euo pipefail
cd "$(dirname "$0")/.."
command -v uv >/dev/null || export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
what="${1:-all}"

if [ "$what" = all ] || [ "$what" = backend ]; then
  echo "== backend"
  uv sync --locked -q
  uv run ruff format --check src tests dev
  uv run ruff check src tests dev
  uv run mypy
  uv run pytest -q -p no:cacheprovider
fi

if [ "$what" = all ] || [ "$what" = web ]; then
  echo "== web"
  (cd web && npm run -s typecheck && npm run -s lint && npm run -s test && npm run -s build >/dev/null)
fi
echo "== CI 检查全部通过"
