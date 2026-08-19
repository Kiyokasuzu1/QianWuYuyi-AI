#!/usr/bin/env bash
# =============================================================================
# start.sh — QianWuYuyi-AI v1.1.0-stable 一键启动（薄封装）
#
# 用法:
#   bash start.sh                 # 后台启动（生产）
#   bash start.sh --foreground    # 前台启动（调试）
#   bash start.sh --no-preflight  # 跳过 preflight 检查
#
# 内部: 若存在 .venv 则激活后调用 scripts/start_yuyi.sh
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [ -f "$ROOT/.venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
fi

exec bash "$ROOT/scripts/start_yuyi.sh" "$@"
