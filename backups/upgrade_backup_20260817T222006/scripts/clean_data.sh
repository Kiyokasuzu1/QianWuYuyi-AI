#!/usr/bin/env bash
# clean_data.sh — Phase D.0 部署前清理脚本
# 用途: 清理历史测试残留, 保留真实记忆数据
# 默认行为:
#   - 清理 tests/ 运行产生的临时数据 (cog_test_*, _e2e_tmp_*, runtime_context/)
#   - 清理 Phase C.10.x 自动化测试产生的 snapshot / feedback 临时目录
#   - 保留 data/memory/, data/personality/, data/self_model/ 等真实数据
#   - 保留 logs/ (供部署后追溯)
#
# 使用:
#   bash scripts/clean_data.sh           # 默认安全模式
#   bash scripts/clean_data.sh --aggressive   # 额外清理 logs/*.log

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

AGGRESSIVE=0
if [[ "${1:-}" == "--aggressive" ]]; then
  AGGRESSIVE=1
fi

echo "============================================================"
echo "  Yuyi Phase D.0 — 部署前数据清理"
echo "============================================================"
echo "  Project root: $PROJECT_ROOT"
echo "  Mode        : $([ $AGGRESSIVE -eq 1 ] && echo 'aggressive' || echo 'safe')"
echo ""

if [[ ! -d "data" ]]; then
  echo -e "${YELLOW}[SKIP]${NC} data/ 目录不存在, 无需清理"
  exit 0
fi

CLEAN_COUNT=0

# 1. 清理 cog_test_*.json
COG_FILES=$(find data -maxdepth 3 -name 'cog_test_*.json' 2>/dev/null || true)
if [[ -n "$COG_FILES" ]]; then
  echo "[1/6] 清理 cog_test_*.json"
  echo "$COG_FILES" | xargs -r rm -f
  CLEAN_COUNT=$((CLEAN_COUNT + $(echo "$COG_FILES" | wc -l)))
fi

# 2. 清理 _e2e_tmp_should_not_exist/
if [[ -d "data/_e2e_tmp_should_not_exist" ]]; then
  echo "[2/6] 清理 data/_e2e_tmp_should_not_exist/"
  rm -rf "data/_e2e_tmp_should_not_exist"
  CLEAN_COUNT=$((CLEAN_COUNT + 1))
fi

# 3. 清理 runtime_context/
if [[ -d "data/runtime_context" ]]; then
  echo "[3/6] 清理 data/runtime_context/"
  rm -rf "data/runtime_context"
  CLEAN_COUNT=$((CLEAN_COUNT + 1))
fi

# 4. 清理 *.tmp / *.bak
TMP_FILES=$(find data -maxdepth 3 \( -name '*.tmp' -o -name '*.bak' \) 2>/dev/null || true)
if [[ -n "$TMP_FILES" ]]; then
  echo "[4/6] 清理 *.tmp / *.bak"
  echo "$TMP_FILES" | xargs -r rm -f
  CLEAN_COUNT=$((CLEAN_COUNT + $(echo "$TMP_FILES" | wc -l)))
fi

# 5. 清理 __pycache__
PYCACHE_COUNT=$(find data -type d -name '__pycache__' 2>/dev/null | wc -l || true)
if [[ "$PYCACHE_COUNT" -gt 0 ]]; then
  echo "[5/6] 清理 data/ 下 __pycache__/ ($PYCACHE_COUNT 个)"
  find data -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
  CLEAN_COUNT=$((CLEAN_COUNT + PYCACHE_COUNT))
fi

# 6. aggressive 模式: 清理 logs/*.log (保留 logs/ 目录)
if [[ $AGGRESSIVE -eq 1 && -d "logs" ]]; then
  LOG_COUNT=$(find logs -maxdepth 1 -name '*.log' 2>/dev/null | wc -l || true)
  if [[ "$LOG_COUNT" -gt 0 ]]; then
    echo "[6/6] [aggressive] 清理 logs/*.log ($LOG_COUNT 个)"
    find logs -maxdepth 1 -name '*.log' -exec rm -f {} + 2>/dev/null || true
    CLEAN_COUNT=$((CLEAN_COUNT + LOG_COUNT))
  fi
else
  echo "[6/6] 保留 logs/ (默认行为)"
fi

echo ""
echo "============================================================"
echo -e "  ${GREEN}清理完成${NC} — 共处理 $CLEAN_COUNT 项"
echo ""
echo "  保留目录 (真实数据):"
echo "    data/memory/         (长期记忆)"
echo "    data/personality/    (人格数据)"
echo "    data/self_model/     (自我模型)"
echo "    data/emotion/        (情绪状态)"
echo "    data/growth/         (成长状态)"
echo "    logs/                (运行日志)"
echo "============================================================"
