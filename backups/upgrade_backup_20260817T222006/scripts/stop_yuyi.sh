#!/usr/bin/env bash
# stop_yuyi.sh — Yuyi 一键停止脚本 (Phase D.0)
# 用途: 停止由 start_yuyi.sh 启动的 API 进程
#
# 用法:
#   bash scripts/stop_yuyi.sh
#   bash scripts/stop_yuyi.sh --force   # 强制 kill -9

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$PROJECT_ROOT/deploy/systemd/installed/yuyi-api.pid"

FORCE=0
for arg in "$@"; do
  case "$arg" in
    --force|-9) FORCE=1 ;;
    *) echo "未知参数: $arg"; exit 1 ;;
  esac
done

if [[ ! -f "$PID_FILE" ]]; then
  echo "[stop_yuyi] 未发现 PID 文件 ($PID_FILE), 无需停止"
  exit 0
fi

PID=$(cat "$PID_FILE" 2>/dev/null || echo "")
if [[ -z "$PID" ]]; then
  echo "[stop_yuyi] PID 文件为空, 清理"
  rm -f "$PID_FILE"
  exit 0
fi

if ! kill -0 "$PID" 2>/dev/null; then
  echo "[stop_yuyi] PID=$PID 进程已不存在, 清理 PID 文件"
  rm -f "$PID_FILE"
  exit 0
fi

if [[ $FORCE -eq 1 ]]; then
  echo "[stop_yuyi] 强制 kill -9 PID=$PID"
  kill -9 "$PID" 2>/dev/null || true
else
  echo "[stop_yuyi] 优雅停止 PID=$PID (SIGTERM, 等待 5s)"
  kill "$PID" 2>/dev/null || true
  for _ in $(seq 1 10); do
    if ! kill -0 "$PID" 2>/dev/null; then
      break
    fi
    sleep 0.5
  done
  if kill -0 "$PID" 2>/dev/null; then
    echo "[stop_yuyi] 进程未退出, 升级到 SIGKILL"
    kill -9 "$PID" 2>/dev/null || true
  fi
fi

rm -f "$PID_FILE"
echo "[stop_yuyi] 已停止 ✓"
