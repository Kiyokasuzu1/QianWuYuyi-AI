#!/bin/bash
# ──────────────────────────────────────────────────────────────────
# Phase 7.2.1-p6-sec 一键部署脚本
# 作用：1) 打 AstrBot user_id 提取补丁  2) 更新羽依 API 文件  3) 双服务重启
#
# 用法：
#   1. 上传以下文件到 /root/QianWuYuyi-AI/ 对应目录：
#        api_server.py               → /root/QianWuYuyi-AI/api_server.py
#        config.yaml                 → /root/QianWuYuyi-AI/config.yaml  (注意保留现有 API Key！)
#        deploy/fix_astrbot_user_id.py → /root/QianWuYuyi-AI/deploy/fix_astrbot_user_id.py
#   2. cd /root/QianWuYuyi-AI && bash deploy/deploy_p6_sec.sh
#
# 回滚（万一 AstrBot 补丁炸了）：
#   cp /root/.local/share/uv/tools/astrbot/lib/python3.12/site-packages/astrbot/core/provider/sources/openai_source.py.bak.p6sec \
#      /root/.local/share/uv/tools/astrbot/lib/python3.12/site-packages/astrbot/core/provider/sources/openai_source.py
#   systemctl restart astrbot
# ──────────────────────────────────────────────────────────────────
set -euo pipefail

PROJECT_DIR="/root/QianWuYuyi-AI"
cd "$PROJECT_DIR"

echo "===== [1/4] 验证文件完整性 ====="
for f in api_server.py config.yaml deploy/fix_astrbot_user_id.py; do
    if [ ! -f "$f" ]; then
        echo "ERROR: 缺少文件 $PROJECT_DIR/$f，先上传！" >&2
        exit 1
    fi
done
echo "  ✓ 所有文件就位"

echo ""
echo "===== [2/4] 应用 AstrBot user_id 补丁 ====="
python3 deploy/fix_astrbot_user_id.py
echo "  ✓ AstrBot 补丁完成"

echo ""
echo "===== [3/4] 重启羽依 API 服务 ====="
systemctl restart yuyi-api.service
sleep 3
if systemctl is-active --quiet yuyi-api.service; then
    echo "  ✓ yuyi-api.service 运行中"
else
    echo "ERROR: yuyi-api.service 启动失败！执行 journalctl -u yuyi-api -n 50 查看日志" >&2
    exit 2
fi

echo ""
echo "===== [4/4] 重启 AstrBot 服务（使 user_id 补丁生效）====="
systemctl restart astrbot.service
sleep 5
if systemctl is-active --quiet astrbot.service; then
    echo "  ✓ astrbot.service 运行中"
else
    echo "WARNING: astrbot 可能仍在初始化中，30 秒后再手动检查：systemctl status astrbot" >&2
fi

echo ""
echo "===== 部署完成！部署版本： ====="
grep -E "system_version|system_version_date|system_version_name" config.yaml

echo ""
echo "===== 快速验证（发一条真实 QQ 身份请求）：====="
echo '  curl -s -X POST http://127.0.0.1:8000/v1/chat/completions \'
echo '    -H "Content-Type: application/json" \'
echo '    -d "{\"model\":\"yuyi\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"user\":\"366648462\"}" \'
echo '    | python3 -c "import sys,json; d=json.load(sys.stdin); print(\"  响应前100字：\", d.get(\"choices\",[{}])[0].get(\"message\",{}).get(\"content\",\"[空]\")[:100])"'

echo ""
echo "===== 查看身份解析日志：====="
echo "  journalctl -u yuyi-api -n 30 | grep -E 'user-id-match|user_id-unknown|user_id-stranger'"
echo ""
echo "  ✅ 你本人的消息（真实 QQ 366648462）应该看到：[user-id-match]"
echo "  ✅ 陌生人的真实 QQ 消息应该看到：[user_id-stranger] + 分配独立沙盒"
echo "  ✅ 身份真的未知时应该看到：[user_id-unknown-strict] → 进 _unknown_sender，绝不兜成清清"
