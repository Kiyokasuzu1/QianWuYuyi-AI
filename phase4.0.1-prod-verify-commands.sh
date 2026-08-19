#!/bin/bash
# ============================================================
# Phase 4.0.1 Step 01 生产验证命令清单
# 用法:在服务器上 bash phase4.0.1-prod-verify-commands.sh
# 或逐段复制执行,把每段输出贴回 Trae
# ============================================================

echo "============================================================"
echo "【验证 1】systemd 服务状态"
echo "============================================================"
systemctl status yuyi-api.service --no-pager
echo ""
echo "--- 服务是否开机自启 ---"
systemctl is-enabled yuyi-api.service
echo ""
echo "--- 服务运行时长 + PID ---"
systemctl show yuyi-api.service --property=ActiveEnterTimestamp,MainPID,ActiveState,SubState

echo ""
echo "============================================================"
echo "【验证 2】进程与端口占用"
echo "============================================================"
echo "--- api_server.py 进程(应只有 1 个) ---"
ps aux | grep -E "api_server\.py" | grep -v grep
echo ""
echo "--- 旧 initiative_sender.py 进程(应为空) ---"
ps aux | grep -E "initiative_sender\.py" | grep -v grep
echo ""
echo "--- 端口 5000 占用(应为 api_server 进程) ---"
ss -tlnp | grep :5000
echo ""
echo "--- 端口 8765 占用(应为同一 api_server 进程) ---"
ss -tlnp | grep :8765
echo ""
echo "--- 5000 和 8765 是否同一 PID ---"
PID_5000=$(ss -tlnp | grep :5000 | grep -oP 'pid=\K[0-9]+' | head -1)
PID_8765=$(ss -tlnp | grep :8765 | grep -oP 'pid=\K[0-9]+' | head -1)
echo "端口 5000 PID = $PID_5000"
echo "端口 8765 PID = $PID_8765"
if [ "$PID_5000" = "$PID_8765" ] && [ -n "$PID_5000" ]; then
  echo "✅ 同一进程占用两端口(单进程架构正确)"
else
  echo "❌ 两端口 PID 不一致或为空(架构异常)"
fi

echo ""
echo "============================================================"
echo "【验证 3】版本号与健康检查"
echo "============================================================"
echo "--- config.yaml 版本号 ---"
grep -E "^system_version" /root/QianWuYuyi-AI/config.yaml
echo ""
echo "--- /health 端点 ---"
curl -s -o - -w "\nHTTP_CODE=%{http_code}\n" http://127.0.0.1:5000/health
echo ""
echo "--- /v1/models 端点 ---"
curl -s -o - -w "\nHTTP_CODE=%{http_code}\n" http://127.0.0.1:5000/v1/models
echo ""
echo "--- /admin/api/agents/status 端点 ---"
curl -s -o - -w "\nHTTP_CODE=%{http_code}\n" http://127.0.0.1:5000/admin/api/agents/status

echo ""
echo "============================================================"
echo "【验证 4】Agent Server 状态文件"
echo "============================================================"
ls -la /root/QianWuYuyi-AI/data/agent_server_status.json 2>&1
echo ""
echo "--- 状态文件内容(应为 enabled=true, running=true, updated_at 新鲜) ---"
cat /root/QianWuYuyi-AI/data/agent_server_status.json 2>&1
echo ""
echo "--- 状态文件最近修改时间(应 ≤5 秒前) ---"
stat /root/QianWuYuyi-AI/data/agent_server_status.json 2>&1 | grep -E "Modify|Access"

echo ""
echo "============================================================"
echo "【验证 5】config.yaml 关键开关"
echo "============================================================"
echo "--- runtime 开关 ---"
grep -E "^\s*enabled|^\s*phase4_enabled|^\s*llm_engine" /root/QianWuYuyi-AI/config.yaml | head -20
echo ""
echo "--- initiative 开关 ---"
grep -A 2 "^initiative:" /root/QianWuYuyi-AI/config.yaml | head -5
echo ""
echo "--- remote / screen 开关 ---"
grep -A 2 "^remote:" /root/QianWuYuyi-AI/config.yaml | head -3
grep -A 2 "^screen:" /root/QianWuYuyi-AI/config.yaml | head -3

echo ""
echo "============================================================"
echo "【验证 6】启动日志检查(最近 100 行)"
echo "============================================================"
echo "--- /root/QianWuYuyi-AI/logs/api_server.log 最近 100 行 ---"
tail -100 /root/QianWuYuyi-AI/logs/api_server.log
echo ""
echo "--- 是否有 3 个 WARNING(personality_resolver.resolve / engine.generate / event_sink.emit) ---"
grep -E "personality_resolver\.resolve|engine\.generate|event_sink\.emit" /root/QianWuYuyi-AI/logs/api_server.log | tail -20
echo ""
echo "--- 是否有 ERROR/CRITICAL ---"
grep -E "ERROR|CRITICAL" /root/QianWuYuyi-AI/logs/api_server.log | tail -20
echo ""
echo "--- systemd journal 最近 50 行 ---"
journalctl -u yuyi-api.service -n 50 --no-pager

echo ""
echo "============================================================"
echo "【验证 7】关键数据文件存在性"
echo "============================================================"
cd /root/QianWuYuyi-AI
for f in data/memory.json data/chroma_db data/self_model data/emotion_state.json data/relationship_state.json data/growth_state.json data/runtime_context; do
  if [ -e "$f" ]; then
    echo "✅ $f 存在"
    ls -la "$f" | head -3
  else
    echo "⚠️  $f 不存在"
  fi
done

echo ""
echo "============================================================"
echo "【验证 8】实际聊天测试(需要 OneBot 已连接)"
echo "============================================================"
echo "--- 发送测试消息(请手动在 QQ 发消息给羽依) ---"
echo "请在 QQ 私聊羽依,发送:"
echo "  1. '羽依你好' (验证人格/身份)"
echo "  2. '我们之前聊过什么?' (验证记忆)"
echo "  3. '今天几号?' (验证基础对话)"
echo ""
echo "--- 同时实时观察日志 ---"
echo "在另一终端执行: tail -f /root/QianWuYuyi-AI/logs/api_server.log"
echo "然后在 QQ 发消息,观察日志中是否出现:"
echo "  - user_id 字段(应为 366648462 或你的 QQ)"
echo "  - MemoryStore.load 或 VectorMemory.search 调用"
echo "  - engine.generate 调用"
echo "  - 回复内容"
echo ""
echo "============================================================"
echo "验证完成。请把以上所有输出复制粘贴回 Trae 对话。"
echo "============================================================"
