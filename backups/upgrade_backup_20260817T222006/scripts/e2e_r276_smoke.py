#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""R2.7.6 端到端本地模拟验证脚本

验证链路：
  模拟 AstrBot → HTTP POST /v1/chat/completions → RuntimeController → response_phase4
    → PersistenceManager → 三态落盘

7 个检查维度：
  1. 模块导入完整性
  2. RuntimeController 单轮 handle_message
  3. Flask HTTP 层（test_client 模拟 AstrBot 请求）
  4. 三态文件落盘（personality / memory / relationship）
  5. 重启后状态保留（销毁 Controller → 重建 → 验证一致）
  6. 多轮记忆关联（Day1 猫娘 → Day3 回复关联）
  7. 并发安全（同用户 2 条消息并发 → 无覆盖丢失）
"""
import sys
import os
import json
import shutil
import tempfile
import threading
import time
from pathlib import Path

# 确保项目根目录在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

PASS = "\033[92m✓ PASS\033[0m"
FAIL = "\033[91m✗ FAIL\033[0m"
WARN = "\033[93m⚠ WARN\033[0m"

results = []


def check(name, condition, detail=""):
    status = PASS if condition else FAIL
    results.append((name, condition, detail))
    print(f"  {status}  {name}" + (f"  ({detail})" if detail and not condition else ""))


def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ──────────────────────────────────────────────
# 1. 模块导入完整性
# ──────────────────────────────────────────────
section("1. 模块导入完整性")

try:
    from src.runtime.runtime_controller import RuntimeController
    check("RuntimeController 导入", True)
except Exception as e:
    check("RuntimeController 导入", False, str(e))
    sys.exit(1)

try:
    from src.response_phase4.persistence_manager import Phase4PersistenceManager
    check("Phase4PersistenceManager 导入", True)
except Exception as e:
    check("Phase4PersistenceManager 导入", False, str(e))

try:
    from src.response_phase4.mock_response_engine import MockResponseEngine
    check("MockResponseEngine 导入", True)
except Exception as e:
    check("MockResponseEngine 导入", False, str(e))

try:
    from src.response_phase4.prompt_renderer import PromptRenderer
    check("PromptRenderer 导入", True)
except Exception as e:
    check("PromptRenderer 导入", False, str(e))

try:
    from src.personality.personality_state import PersonalityState
    check("PersonalityState 导入", True)
except Exception as e:
    check("PersonalityState 导入", False, str(e))

try:
    from src.response_phase4.deepseek_adapter import DeepSeekAdapter
    check("DeepSeekAdapter 导入", True)
except Exception as e:
    check("DeepSeekAdapter 导入", False, str(e))

try:
    from tests.support.continuous_loop_runner import ContinuousLoopRunner
    check("ContinuousLoopRunner 导入", True)
except Exception as e:
    check("ContinuousLoopRunner 导入", False, str(e))


# ──────────────────────────────────────────────
# 准备：临时数据目录
# ──────────────────────────────────────────────
tmp_root = Path(tempfile.mkdtemp(prefix="e2e_r276_"))
users_dir = tmp_root / "users"
users_dir.mkdir(parents=True, exist_ok=True)

print(f"\n  临时数据目录: {tmp_root}")


def make_controller(phase4_enabled=True, llm_engine="mock"):
    """构造一个干净的 RuntimeController 单例"""
    RuntimeController._instance = None
    return RuntimeController(config={
        "runtime": {
            "phase4_enabled": phase4_enabled,
            "users_root_dir": str(users_dir),
            "llm_engine": llm_engine,
            "growth_enabled": True,
            "legacy_memory_auto_import": False,
            "max_context_memories": 50,
            "deepseek": {
                "enabled": llm_engine == "deepseek",
                "max_retries": 3,
                "timeout_seconds": 30,
                "max_tokens": 2048,
            },
        }
    })


# ──────────────────────────────────────────────
# 2. RuntimeController 单轮 handle_message
# ──────────────────────────────────────────────
section("2. RuntimeController 单轮 handle_message")

ctrl = make_controller()
hr = ctrl.handle_message(
    user_id="qq_10001",
    message="你好羽依，我叫清夏，今天想聊聊角色设计",
)

check("reply 非空", bool(hr.reply) and len(hr.reply) > 5,
      f"len={len(hr.reply) if hr.reply else 0}")
check("reply 是字符串", isinstance(hr.reply, str))
check("debug 含 turn_uuid", "turn_uuid" in hr.debug)
check("debug 含 user_id", hr.debug.get("user_id") == "qq_10001",
      f"got {hr.debug.get('user_id')}")
print(f"\n  回复内容: {hr.reply[:120]}...")

# DEBUG: 检查 PM 实际路径
pm = ctrl._get_pm_for_user("qq_10001")
print(f"  [DEBUG] PM snapshot_dir: {pm.snapshot_dir}")
print(f"  [DEBUG] personality_file: {pm.personality_file} (exists={pm.personality_file.exists()})")


# ──────────────────────────────────────────────
# 3. Flask HTTP 层（test_client 模拟 AstrBot 请求）
# ──────────────────────────────────────────────
section("3. Flask HTTP 层（模拟 AstrBot → POST /v1/chat/completions）")

try:
    from api_server import app, _runtime_controller

    # 确保 app 里有 controller（可能 init_orchestrator 没跑）
    if _runtime_controller is None:
        # 手动注入
        import api_server as _api_mod
        _api_mod._runtime_controller = ctrl

    with app.test_client() as client:
        resp = client.post("/v1/chat/completions", json={
            "messages": [
                {"role": "user", "content": "羽依你觉得猫娘这个设定怎么样"}
            ],
            "user": "qq_10001",
            "debug": True,
        })

        check("HTTP 200", resp.status_code == 200,
              f"status={resp.status_code}")

        body = resp.get_json()
        if body and "choices" in body:
            check("返回 OpenAI 兼容结构", len(body["choices"]) > 0)
            content = body["choices"][0]["message"]["content"]
            check("assistant 回复非空", bool(content) and len(content) > 5,
                  f"len={len(content) if content else 0}")
            check("model 含 phase4", "phase4" in body.get("model", ""),
                  f"model={body.get('model')}")
            print(f"\n  HTTP 回复: {content[:120]}...")
        else:
            check("返回 OpenAI 兼容结构", False,
                  f"body={str(body)[:200]}")
except Exception as e:
    check("Flask test_client", False, str(e))
    import traceback
    traceback.print_exc()


# ──────────────────────────────────────────────
# 4. 三态文件落盘
# ──────────────────────────────────────────────
section("4. 三态文件落盘（data/users/<uid>/）")

# 用 PM 属性获取真实文件路径（文件名含 user_tag 后缀）
user_dir = users_dir / "qq_10001"
personality_file = pm.personality_file
memory_file = pm.memory_file
relationship_file = pm.relationship_file

check("personality.json 存在", personality_file.exists())
check("memory.json 存在", memory_file.exists())
check("relationship.json 存在", relationship_file.exists())

if personality_file.exists():
    ps = json.loads(personality_file.read_text(encoding="utf-8"))
    check("personality 含 traits", "traits" in ps or "core_traits" in ps,
          f"keys={list(ps.keys())[:5]}")

if memory_file.exists():
    mem = json.loads(memory_file.read_text(encoding="utf-8"))
    records = mem.get("records", [])
    check(f"memory 有记录 (count={len(records)})", len(records) > 0)

if relationship_file.exists():
    rel = json.loads(relationship_file.read_text(encoding="utf-8"))
    check("relationship 含 trust", "trust" in rel or "trust_level" in rel,
          f"keys={list(rel.keys())[:8]}")


# ──────────────────────────────────────────────
# 5. 重启后状态保留
# ──────────────────────────────────────────────
section("5. 重启后状态保留（销毁 Controller → 重建 → 验证一致）")

# 读取当前 personality 快照
ps_before = json.loads(personality_file.read_text(encoding="utf-8"))
mem_before = json.loads(memory_file.read_text(encoding="utf-8"))
rel_before = json.loads(relationship_file.read_text(encoding="utf-8"))

# 模拟重启：销毁 Controller 单例，重新构造
ctrl2 = make_controller()

# 再发一条消息
hr2 = ctrl2.handle_message(
    user_id="qq_10001",
    message="今天天气不错呢",
)

check("重启后 reply 非空", bool(hr2.reply) and len(hr2.reply) > 5)

# 验证 personality 没有被重置（traits 值应该延续）
ps_after = json.loads(personality_file.read_text(encoding="utf-8"))
mem_after = json.loads(memory_file.read_text(encoding="utf-8"))

# 提取 traits 做比较
def extract_traits(ps_dict):
    if "traits" in ps_dict:
        return ps_dict["traits"]
    if "core_traits" in ps_dict:
        return ps_dict["core_traits"]
    return {}

traits_before = extract_traits(ps_before)
traits_after = extract_traits(ps_after)

if traits_before and traits_after:
    # 至少有一个 trait 值保持一致或合理增长（不能被重置为默认值）
    shared_keys = set(traits_before.keys()) & set(traits_after.keys())
    reset_count = 0
    for k in shared_keys:
        v_before = float(traits_before[k])
        v_after = float(traits_after[k])
        # 如果值被重置为 0.5 且之前不是 0.5，说明丢了
        if abs(v_after - 0.5) < 0.001 and abs(v_before - 0.5) > 0.05:
            reset_count += 1
    check(f"traits 未被重置 (reset_count={reset_count})", reset_count == 0)
else:
    check("traits 提取", False, "before 或 after traits 为空")

# memory 应该累积（after >= before）
check(f"memory 累积 (before={len(mem_before.get('records',[]))}, after={len(mem_after.get('records',[]))})",
      len(mem_after.get("records", [])) >= len(mem_before.get("records", [])))


# ──────────────────────────────────────────────
# 6. 多轮记忆关联（Day1 猫娘 → Day3 回复关联）
# ──────────────────────────────────────────────
section("6. 多轮记忆关联（Day1 猫娘 → Day3 自然关联）")

ctrl3 = make_controller()

# Day1: 聊猫娘设计
hr_d1 = ctrl3.handle_message(
    user_id="qq_20002",
    message="我特别喜欢猫娘设计，耳朵和尾巴超级可爱",
)
check("Day1 reply 非空", bool(hr_d1.reply))

# Day2: 随便聊别的
hr_d2 = ctrl3.handle_message(
    user_id="qq_20002",
    message="今天去公园散步了，天气很好",
)
check("Day2 reply 非空", bool(hr_d2.reply))

# Day3: 问一个关联性问题
hr_d3 = ctrl3.handle_message(
    user_id="qq_20002",
    message="你觉得什么角色设计比较有魅力？",
)
check("Day3 reply 非空", bool(hr_d3.reply))

# 检查 Day3 回复是否有关联关键词
# Mock 引擎回复是固定模板，不读 memory 内容，所以这里只验证 memory 落盘有关联记录
reply_d3 = hr_d3.reply
print(f"\n  Day3 回复: {reply_d3[:200]}...")

# 验证 user_20002 的 memory 里有猫娘相关记录（Mock 引擎不读 memory，但 PersistenceManager 应保存）
pm_20002 = ctrl3._get_pm_for_user("qq_20002")
mem_20002_file = pm_20002.memory_file
if mem_20002_file.exists():
    mem_data = json.loads(mem_20002_file.read_text(encoding="utf-8"))
    records = mem_data.get("records", [])
    all_text = json.dumps(records, ensure_ascii=False)
    has_cat = "猫" in all_text or "猫娘" in all_text
    check(f"memory 含猫娘记录 (total_records={len(records)})", has_cat)
    check(f"Day3 memory 记录数 >= 3 (got {len(records)})", len(records) >= 3)
else:
    check("memory 文件存在", False, f"path={mem_20002_file}")


# ──────────────────────────────────────────────
# 7. 并发安全（同用户 2 条消息并发）
# ──────────────────────────────────────────────
section("7. 并发安全（同用户 2 条消息并发 → 无覆盖）")

ctrl4 = make_controller()

errors = []
replies_concurrent = []

def send_msg(msg_text, idx):
    try:
        hr = ctrl4.handle_message(user_id="qq_30003", message=msg_text)
        replies_concurrent.append((idx, hr.reply, hr.debug.get("turn_uuid", "")))
    except Exception as e:
        errors.append((idx, str(e)))

t1 = threading.Thread(target=send_msg, args=("你好我是小红，今天想聊聊天", 0))
t2 = threading.Thread(target=send_msg, args=("今天聊点音乐吧，最近听了好多歌", 1))

t1.start()
t2.start()
t1.join(timeout=30)
t2.join(timeout=30)

check("并发无异常", len(errors) == 0,
      "; ".join([f"[{i}] {e}" for i, e in errors]))
check("两条都返回 reply", len(replies_concurrent) == 2,
      f"got {len(replies_concurrent)}")
check("两条 turn_uuid 不同",
      len(set(r[2] for r in replies_concurrent)) == 2,
      f"uuids={[r[2] for r in replies_concurrent]}")

# 验证 memory 没有丢失记录（应该有 >= 2 条）
pm_30003 = ctrl4._get_pm_for_user("qq_30003")
mem_30003_file = pm_30003.memory_file
if mem_30003_file.exists():
    mem_data = json.loads(mem_30003_file.read_text(encoding="utf-8"))
    records = mem_data.get("records", [])
    check(f"并发后 memory 记录完整 (count={len(records)})", len(records) >= 2)
else:
    check("并发后 memory 文件存在", False, f"path={mem_30003_file}")


# ──────────────────────────────────────────────
# 汇总
# ──────────────────────────────────────────────
section("汇总")

total = len(results)
passed = sum(1 for _, ok, _ in results if ok)
failed = total - passed

print(f"\n  总计: {total}  通过: {passed}  失败: {failed}")
print()

if failed == 0:
    print(f"  \033[92m{'='*50}")
    print(f"  R2.7.6 端到端验证全部通过 ✓")
    print(f"  羽依可以从实验环境进入真实运行链路了")
    print(f"  {'='*50}\033[0m")
else:
    print(f"  \033[91m{'='*50}")
    print(f"  有 {failed} 项失败，需要修复后再上线")
    print(f"  {'='*50}\033[0m")
    for name, ok, detail in results:
        if not ok:
            print(f"    ✗ {name}: {detail}")

# 清理
shutil.rmtree(tmp_root, ignore_errors=True)
print(f"\n  临时目录已清理: {tmp_root}")

sys.exit(0 if failed == 0 else 1)
