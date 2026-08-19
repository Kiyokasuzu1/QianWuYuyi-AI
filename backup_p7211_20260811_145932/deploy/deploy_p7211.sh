#!/bin/bash
# ──────────────────────────────────────────────────────────────────
# Phase 7.2.1.1-identity-stabilization 全量部署脚本
#
# 用法：
#   1. 通过宝塔面板上传 p7211_full_deploy.zip 到 /root/QianWuYuyi-AI/
#   2. cd /root/QianWuYuyi-AI && bash deploy/deploy_p7211.sh
#
# 回滚：
#   bash deploy/deploy_p7211.sh --rollback
# ──────────────────────────────────────────────────────────────────
set -euo pipefail

PROJECT_DIR="/root/QianWuYuyi-AI"
BACKUP_DIR="$PROJECT_DIR/backup_p7211_$(date +%Y%m%d_%H%M%S)"
cd "$PROJECT_DIR"

# ── 回滚模式 ──
if [ "${1:-}" = "--rollback" ]; then
    echo "===== 回滚模式 ====="
    LATEST_BACKUP=$(ls -dt "$PROJECT_DIR"/backup_p7211_* 2>/dev/null | head -1)
    if [ -z "$LATEST_BACKUP" ]; then
        echo "ERROR: 找不到 backup_p7211_* 备份目录！" >&2
        exit 1
    fi
    echo "  从 $LATEST_BACKUP 恢复..."
    cp -rf "$LATEST_BACKUP/src" "$PROJECT_DIR/"
    cp -f "$LATEST_BACKUP/api_server.py" "$PROJECT_DIR/" 2>/dev/null || true
    cp -f "$LATEST_BACKUP/config.yaml" "$PROJECT_DIR/" 2>/dev/null || true
    systemctl restart yuyi-api.service
    sleep 3
    if systemctl is-active --quiet yuyi-api.service; then
        echo "  ✓ 回滚完成，yuyi-api 运行中"
    else
        echo "ERROR: 回滚后 yuyi-api 启动失败！" >&2
        exit 2
    fi
    exit 0
fi

# ── 正常部署 ──
echo "===== [1/6] 检查部署包 ====="
ZIP_FILE="$PROJECT_DIR/p7211_full_deploy.zip"
if [ ! -f "$ZIP_FILE" ]; then
    echo "ERROR: 找不到 p7211_full_deploy.zip" >&2
    exit 1
fi
echo "  ✓ 部署包存在 ($(du -h "$ZIP_FILE" | cut -f1))"

echo ""
echo "===== [2/6] 备份现有代码 ====="
mkdir -p "$BACKUP_DIR/src" "$BACKUP_DIR/deploy"
# 备份核心目录
cp -r "$PROJECT_DIR/src" "$BACKUP_DIR/"
cp -f "$PROJECT_DIR/api_server.py" "$BACKUP_DIR/" 2>/dev/null || true
cp -f "$PROJECT_DIR/config.yaml" "$BACKUP_DIR/" 2>/dev/null || true
cp -r "$PROJECT_DIR/deploy" "$BACKUP_DIR/" 2>/dev/null || true
cp -r "$PROJECT_DIR/tests" "$BACKUP_DIR/" 2>/dev/null || true
echo "  ✓ 备份完成 → $BACKUP_DIR"

echo ""
echo "===== [3/6] 解压全量包（不覆盖 data/logs/venv/.git）====="
# 解压时排除服务器端不应覆盖的目录
unzip -o -q "$ZIP_FILE" \
    -x "data/*" \
       "logs/*" \
       "venv/*" \
       ".git/*" \
       "__pycache__/*" \
       "*.pyc" \
       "backup_p7211_*/*" \
    -d "$PROJECT_DIR/"
echo "  ✓ 解压完成"

echo ""
echo "===== [4/6] 验证 Python 语法 ====="
VENV_PYTHON="$PROJECT_DIR/venv/bin/python"
if [ ! -f "$VENV_PYTHON" ]; then
    VENV_PYTHON="python3"
fi
SYNTAX_FILES=(
    "api_server.py"
    "src/orchestrator.py"
    "src/runtime/runtime_pipeline.py"
    "src/runtime/runtime_controller.py"
    "src/response_phase4/prompt_renderer.py"
    "src/response_phase4/rendered_prompt_schema.py"
    "src/context/prompt_context_schema.py"
    "src/identity/origin_facade.py"
    "src/identity/origin_identity.py"
)
for f in "${SYNTAX_FILES[@]}"; do
    if ! "$VENV_PYTHON" -c "import py_compile; py_compile.compile('$f', doraise=True)" 2>/dev/null; then
        echo "ERROR: $f 语法检查失败！" >&2
        "$VENV_PYTHON" -c "import py_compile; py_compile.compile('$f', doraise=True)"
        exit 3
    fi
done
echo "  ✓ 所有关键文件语法正确"

echo ""
echo "===== [5/6] 重启 yuyi-api 服务 ====="
systemctl restart yuyi-api.service
sleep 4
if systemctl is-active --quiet yuyi-api.service; then
    echo "  ✓ yuyi-api.service 运行中"
else
    echo "ERROR: yuyi-api.service 启动失败！" >&2
    echo "  最近日志：" >&2
    journalctl -u yuyi-api -n 30 --no-pager >&2
    exit 2
fi

echo ""
echo "===== [6/6] 快速验证 ====="
echo ""
echo "--- 版本信息 ---"
grep -E "^system_version" config.yaml || echo "  (未找到 system_version)"

echo ""
echo "--- 服务状态 ---"
systemctl is-active yuyi-api.service

echo ""
echo "--- 端口监听 ---"
ss -tlnp | grep -E ':(8000|8765)' || echo "  (端口检查需要 root 权限)"

echo ""
echo "--- 启动日志（最后20行）---"
journalctl -u yuyi-api -n 20 --no-pager

echo ""
echo "===== 部署完成！====="
echo ""
echo "下一步验证命令："
echo ""
echo "  # V1: 发一条测试消息（模拟清清）"
echo "  curl -s -X POST http://127.0.0.1:8000/v1/chat/completions \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"model\":\"yuyi\",\"messages\":[{\"role\":\"user\",\"content\":\"你知道我是谁吗？\"}],\"user\":\"366648462\"}' \\"
echo "    | python3 -c \"import sys,json; d=json.load(sys.stdin); print('回复:', d.get('choices',[{}])[0].get('message',{}).get('content','[空]')[:200])\""
echo ""
echo "  # V2: 查看身份解析日志"
echo "  journalctl -u yuyi-api -n 50 --no-pager | grep -E 'CognitiveContext|identity_source|处理用户|OriginFacade'"
echo ""
