#!/usr/bin/env bash
# start_yuyi.sh — Yuyi 一键启动脚本 (Phase D.0)
# 用途: 部署后通过 systemd 或直接命令行启动羽依 API 服务
#
# 用法:
#   bash scripts/start_yuyi.sh                    # 后台启动 (生产)
#   bash scripts/start_yuyi.sh --foreground       # 前台启动 (调试)
#   bash scripts/start_yuyi.sh --no-preflight     # 跳过 preflight 检查
#
# 设计:
#   - 默认后台启动, 写 PID 到 deploy/systemd/installed/yuyi-api.pid
#   - 启动前自动执行 preflight_check (除非 --no-preflight)
#   - 日志输出到 logs/yuyi-api.log
#   - 健康检查可选追加: bash scripts/start_yuyi.sh --with-healthcheck

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

PID_DIR="$PROJECT_ROOT/deploy/systemd/installed"
PID_FILE="$PID_DIR/yuyi-api.pid"
LOG_DIR="$PROJECT_ROOT/logs"
LOG_FILE="$LOG_DIR/yuyi-api.log"
HEALTH_SCRIPT="$PROJECT_ROOT/scripts/health_check.py"
PREFLIGHT_SCRIPT="$PROJECT_ROOT/scripts/preflight_check.py"
API_PORT="${YUYI_API_PORT:-5000}"

FOREGROUND=0
NO_PREFLIGHT=0
WITH_HEALTHCHECK=0

for arg in "$@"; do
  case "$arg" in
    --foreground|-f) FOREGROUND=1 ;;
    --no-preflight)  NO_PREFLIGHT=1 ;;
    --with-healthcheck) WITH_HEALTHCHECK=1 ;;
    -h|--help)
      sed -n '2,18p' "$0"
      exit 0
      ;;
    *) echo "未知参数: $arg"; exit 1 ;;
  esac
done

mkdir -p "$PID_DIR" "$LOG_DIR"

# 1. 加载 .env (若存在)
if [[ -f "$PROJECT_ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_ROOT/.env"
  set +a
  echo "[start_yuyi] 已加载 .env"
fi

# 2. 检查是否已在运行
if [[ -f "$PID_FILE" ]]; then
  OLD_PID=$(cat "$PID_FILE" 2>/dev/null || echo "")
  if [[ -n "$OLD_PID" ]] && kill -0 "$OLD_PID" 2>/dev/null; then
    echo "[start_yuyi] Yuyi API 已在运行 (PID=$OLD_PID)"
    exit 0
  else
    echo "[start_yuyi] 清理陈旧 PID 文件"
    rm -f "$PID_FILE"
  fi
fi

# 3. Preflight 检查
if [[ $NO_PREFLIGHT -eq 0 && -f "$PREFLIGHT_SCRIPT" ]]; then
  echo "[start_yuyi] 执行 preflight 检查..."
  if ! python3 "$PREFLIGHT_SCRIPT" --exit-code; then
    echo "[start_yuyi] preflight 失败 — 终止启动"
    echo "  提示: 使用 --no-preflight 跳过"
    exit 1
  fi
fi

# 4. 启动 API 服务
echo "[start_yuyi] 启动 Yuyi API (port=$API_PORT, foreground=$FOREGROUND)"

if [[ $FOREGROUND -eq 1 ]]; then
  exec python3 -m api_server
else
  nohup python3 -m api_server >> "$LOG_FILE" 2>&1 &
  NEW_PID=$!
  echo "$NEW_PID" > "$PID_FILE"
  echo "[start_yuyi] 已启动 (PID=$NEW_PID, log=$LOG_FILE)"

  # 5. 等待并健康检查
  sleep 3
  if kill -0 "$NEW_PID" 2>/dev/null; then
    echo "[start_yuyi] 进程存活 ✓"
  else
    echo "[start_yuyi] 进程已退出 — 请查看 $LOG_FILE"
    exit 1
  fi

  if [[ $WITH_HEALTHCHECK -eq 1 && -f "$HEALTH_SCRIPT" ]]; then
    echo "[start_yuyi] 执行 health check..."
    python3 "$HEALTH_SCRIPT" --base "http://127.0.0.1:$API_PORT" || true
  fi
fi
