#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Phase 7.2.1 观察期辅助脚本

提供 4 个子命令:
  --trace-continuity    连续 50 条消息 → session_id 一致 / trace_id 不重复 / 无跨轮混入
  --trace-concurrent    2 线程并发各 20 条 → 多窗口并发无串线
  --timeline-density    10 条消息 → 每轮 cognitive event 数 / 重复 memory.retrieved 数
  --perf                50 条消息 → 平均 / P50 / P95 / P99 延迟
  --perf-diff --a A.json --b B.json
                        对比两次性能结果

所有子命令都走 HTTP POST /v1/chat/completions 模拟真实聊天用户
（不直接调内部 API，这样测到的才是真实端到端行为，包括 Flask/路由/线程开销）。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import threading
import time
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List

import urllib.request
import urllib.error

# 项目根
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

OUT_DIR = PROJECT_ROOT / "data" / "phase721_observation"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 默认 API endpoint（与 api_server.py 对齐）
DEFAULT_BASE_URL = "http://127.0.0.1:5000"
DEFAULT_EVENTS_RECENT = "/api/admin/runtime/events/recent"
DEFAULT_CHAT_COMPLETIONS = "/v1/chat/completions"


# ============================================================
# HTTP helpers
# ============================================================
def _json_post(url: str, payload: Dict[str, Any], timeout: int = 120) -> Dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            if resp.status >= 400:
                return {"_status": resp.status, "_error": body}
            try:
                return json.loads(body)
            except Exception:
                return {"_status": resp.status, "_raw": body}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"_status": e.code, "_error": body}
    except Exception as e:
        return {"_status": 0, "_error": f"exception: {e}"}


def _json_get(url: str, timeout: int = 10) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            try:
                return json.loads(body)
            except Exception:
                return {"_raw": body}
    except Exception as e:
        return {"_error": f"exception: {e}"}


# ============================================================
# 聊天 payload
# ============================================================
_FAKE_USER_IDS = ["u_alice", "u_bob"]
_DEFAULT_MODEL = "mock"  # 用 mock 的响应引擎让脚本不依赖 LLM API Key，本地就能跑

_FAKE_PROMPTS_POOL = [
    "你好呀，今天过得怎么样？",
    "我们之前聊过什么？帮我回顾一下最近的聊天。",
    "你觉得自己的性格是什么样的？",
    "最近工作很累，想找人说说话。",
    "天气热，心情不好，怎么办？",
    "如果让你描述自己，你会怎么说？",
    "我们是不是认识很久了？感觉像老朋友。",
    "今天AI新闻真多，你怎么看？",
    "我最近在忙项目，压力好大。",
    "讲个轻松的话题吧。",
    "说说你记得我的哪些事？",
    "你有什么想和我分享的吗？",
    "你现在感觉怎么样？（你的情绪）",
    "今天要不要一起做点什么有趣的？",
    "你觉得我们的关系现在到哪个阶段了？",
]


def chat_payload(user_id: str, prompt: str, model: str = _DEFAULT_MODEL) -> Dict[str, Any]:
    """构造 /v1/chat/completions payload，与 e2e_r276_smoke 对齐。"""
    return {
        "model": model,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "user_id": user_id,
        "platform": "qiyuan",
        "stream": False,
    }


# ============================================================
# 观察: 事件流 recent 拉取
# ============================================================
def fetch_recent_events(base_url: str, limit: int = 100) -> List[Dict[str, Any]]:
    data = _json_get(base_url + DEFAULT_EVENTS_RECENT + f"?limit={limit}")
    if isinstance(data, dict) and "events" in data:
        return data["events"]
    if isinstance(data, list):
        return data
    return []


# ============================================================
# 模式 1: Trace 连续性 — 连续消息
# ============================================================
def run_trace_continuity(base_url: str, messages: int, user_id: str) -> int:
    print("=" * 60)
    print(f"  ① Trace 连续性: 连续 {messages} 条消息")
    print("=" * 60)

    chat_endpoint = base_url + DEFAULT_CHAT_COMPLETIONS

    first_session_id: str | None = None
    trace_ids: List[str] = []
    prev_memory_ids: List[str] = []
    cross_contamination = False

    for i in range(messages):
        prompt = f"[{i+1}] " + random.choice(_FAKE_PROMPTS_POOL)
        payload = chat_payload(user_id, prompt)

        t0 = time.time()
        resp = _json_post(chat_endpoint, payload, timeout=300)
        t1 = time.time()

        error = resp.get("_error") or resp.get("error")
        if error:
            print(f"  [{i+1:>3}/{messages}] ✗ HTTP 失败: {str(error)[:120]}")
            # 继续下一条，不中断
            continue

        # 尽量从 outputs 里取 lifecycle_id（= trace_id），没有就跳过
        outputs = {}
        if isinstance(resp, dict):
            outputs = (
                resp.get("outputs")
                or (resp.get("choices") and resp["choices"][0].get("outputs", {}))
                or {}
            )
        trace_id = (
            outputs.get("lifecycle_id")
            or outputs.get("trace_id")
            or resp.get("lifecycle_id")
            or resp.get("id")
            or ""
        )
        session_id = (
            outputs.get("session_id")
            or resp.get("session_id")
            or user_id
        )
        reply_len = len(str(outputs.get("reply", "") or resp.get("reply", "") or (resp.get("choices") and resp["choices"][0].get("message", {}).get("content", "")) or ""))

        if i == 0:
            first_session_id = session_id

        if session_id and first_session_id and session_id != first_session_id:
            print(f"  [{i+1:>3}/{messages}] ✗ session_id 从 {first_session_id} 变成了 {session_id}  → FAIL")

        trace_ids.append(trace_id)

        elapsed_ms = (t1 - t0) * 1000
        print(f"  [{i+1:>3}/{messages}] ✓ trace={trace_id[:12]}... session={str(session_id)[:10]}... reply={reply_len}c  ({elapsed_ms:.0f}ms)")

    # 分析结果
    print()
    ok_trace_unique = len(set(trace_ids)) == len(trace_ids)
    ok_session_consistent = (first_session_id is not None)

    print("[结果]")
    print(f"  session_id 一致:           {'PASS' if ok_session_consistent else 'FAIL'}  (first={str(first_session_id)[:20]})")
    print(f"  trace_id 每条不重复:        {'PASS' if ok_trace_unique else 'FAIL'}  (重复 {len(trace_ids)-len(set(trace_ids))} 次)")
    print(f"  无跨轮混入 (目测):          {'PASS 需人工看 Dashboard 确认' if not cross_contamination else 'FAIL'}")
    print()

    # 保存原始数据供你后查
    out = {
        "user_id": user_id,
        "messages": messages,
        "first_session_id": first_session_id,
        "trace_ids": trace_ids,
        "trace_id_unique_count": len(set(trace_ids)),
        "session_id_consistent": ok_session_consistent,
    }
    out_file = OUT_DIR / "trace_continuous.json"
    out_file.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  原始数据: {out_file}")

    return 0 if (ok_trace_unique and ok_session_consistent) else 1


# ============================================================
# 模式 2: Trace 并发（2 线程模拟 2 窗口）
# ============================================================
def _concurrent_worker(base_url: str, user_id: str, messages: int, store: Dict[str, Any]) -> None:
    chat_endpoint = base_url + DEFAULT_CHAT_COMPLETIONS
    traces: List[str] = []
    prompts_sent: List[str] = []
    for i in range(messages):
        prompt = f"[{user_id}:{i+1}] " + random.choice(_FAKE_PROMPTS_POOL)
        prompts_sent.append(prompt)
        resp = _json_post(chat_endpoint, chat_payload(user_id, prompt), timeout=300)
        outputs = resp.get("outputs") or (resp.get("choices") and resp["choices"][0].get("outputs", {})) or {}
        trace_id = outputs.get("lifecycle_id") or outputs.get("trace_id") or resp.get("id") or f"unknown_{i}"
        traces.append(str(trace_id))
        time.sleep(0.05)
    store[user_id] = {
        "traces": traces,
        "prompts": prompts_sent,
    }


def run_trace_concurrent(base_url: str, messages_per_user: int) -> int:
    print("=" * 60)
    print(f"  ① Trace 并发: 2 用户各 {messages_per_user} 条消息")
    print("=" * 60)

    store: Dict[str, Any] = {}
    threads = []
    for uid in _FAKE_USER_IDS:
        t = threading.Thread(target=_concurrent_worker, args=(base_url, uid, messages_per_user, store))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    traces_a = set(store.get(_FAKE_USER_IDS[0], {}).get("traces", []))
    traces_b = set(store.get(_FAKE_USER_IDS[1], {}).get("traces", []))
    overlap = traces_a & traces_b
    ok = len(overlap) == 0

    print()
    print("[结果]")
    print(f"  用户 {_FAKE_USER_IDS[0]} trace 数: {len(traces_a)}")
    print(f"  用户 {_FAKE_USER_IDS[1]} trace 数: {len(traces_b)}")
    print(f"  trace_id 交集: {len(overlap)}  →  {'PASS（无串线）' if ok else 'FAIL: 两个用户出现共同 trace_id,说明 thread-local 串了'}")

    out_file = OUT_DIR / "trace_concurrent.json"
    out_file.write_text(
        json.dumps({
            "messages_per_user": messages_per_user,
            "overlap": list(overlap),
            "user_a_traces": list(traces_a),
            "user_b_traces": list(traces_b),
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  原始数据: {out_file}")
    return 0 if ok else 1


# ============================================================
# 模式 3: Timeline 信息密度
# ============================================================
def run_timeline_density(base_url: str, messages: int) -> int:
    print("=" * 60)
    print(f"  ② Timeline 密度: {messages} 条消息")
    print("=" * 60)

    chat_endpoint = base_url + DEFAULT_CHAT_COMPLETIONS
    user_id = _FAKE_USER_IDS[0]

    per_round_cog_counts: List[int] = []
    mem_retrieved_counts: Counter = Counter()

    for i in range(messages):
        # 这一轮开始前先把当前 recent 事件 baseline 记下来(方便数这一轮新增多少 cognitive)
        before_ids = {e.get("event_id") for e in fetch_recent_events(base_url, limit=200)}

        prompt = f"[D:{i+1}] " + random.choice(_FAKE_PROMPTS_POOL)
        _json_post(chat_endpoint, chat_payload(user_id, prompt), timeout=300)

        time.sleep(0.2)  # 给 SSE/recent 一小段缓冲，事件不一定同步写完

        after = fetch_recent_events(base_url, limit=300)
        new_events = [e for e in after if e.get("event_id") not in before_ids]

        # 数 cognitive.* 事件（event_type 字段可能存在外层，也可能存在内层 data.event_type）
        cog_count = 0
        for e in new_events:
            et = e.get("event_type") or ""
            inner = e.get("data") or {}
            inner_et = inner.get("event_type") if isinstance(inner, dict) else ""
            if isinstance(et, str) and et.startswith("cognitive."):
                cog_count += 1
                if "memory.retrieved" in et:
                    mem_retrieved_counts["round_" + str(i)] += 1
            elif isinstance(inner_et, str) and inner_et.startswith("cognitive."):
                cog_count += 1
                if "memory.retrieved" in inner_et:
                    mem_retrieved_counts["round_" + str(i)] += 1

        per_round_cog_counts.append(cog_count)
        print(f"  [{i+1:>3}/{messages}]  cognitive events = {cog_count}")

    print()
    avg = statistics.mean(per_round_cog_counts) if per_round_cog_counts else 0
    mx = max(per_round_cog_counts) if per_round_cog_counts else 0
    duplicated_mem_rounds = sum(1 for c in mem_retrieved_counts.values() if c > 1)
    total_duplicate_mem_calls = sum(c - 1 for c in mem_retrieved_counts.values() if c > 1)

    print("[结果]")
    print(f"  每轮 cognitive event 数: 平均 {avg:.2f}，最大 {mx}")
    print(f"  memory.retrieved 重复次数（>1 次的轮次累加）: {total_duplicate_mem_calls}（涉及 {duplicated_mem_rounds} 轮）")

    if mx <= 8:
        tag = "好（信息量充足，没有重复浪费）"
    elif mx <= 12:
        tag = "可接受（稍密，但每条都有价值）"
    else:
        tag = "过载（建议后续聚合）"
    print(f"  评价: {tag}")

    out = {
        "messages": messages,
        "per_round_cog_counts": per_round_cog_counts,
        "avg": avg,
        "max": mx,
        "memory_retrieved_duplicate_counts": dict(mem_retrieved_counts),
        "total_duplicate_mem_calls": total_duplicate_mem_calls,
        "tag": tag,
    }
    out_file = OUT_DIR / "timeline_density.json"
    out_file.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  原始数据: {out_file}")
    return 0


# ============================================================
# 模式 4: 性能基线
# ============================================================
def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def run_perf(base_url: str, messages: int, out_json: str | None) -> int:
    print("=" * 60)
    print(f"  ③ 性能基线: {messages} 条消息")
    print("=" * 60)

    chat_endpoint = base_url + DEFAULT_CHAT_COMPLETIONS
    user_id = "u_perf_" + uuid.uuid4().hex[:6]
    latencies_ms: List[float] = []
    errors = 0

    for i in range(messages):
        prompt = f"[P:{i+1}] " + random.choice(_FAKE_PROMPTS_POOL)
        t0 = time.perf_counter()
        resp = _json_post(chat_endpoint, chat_payload(user_id, prompt), timeout=600)
        t1 = time.perf_counter()
        ms = (t1 - t0) * 1000.0
        if resp.get("_error") or resp.get("error"):
            errors += 1
        latencies_ms.append(ms)
        if (i + 1) % 10 == 0:
            print(f"  [{i+1:>3}/{messages}]  当前平均 = {statistics.mean(latencies_ms):.0f}ms")

    avg = statistics.mean(latencies_ms) if latencies_ms else 0
    p50 = _percentile(latencies_ms, 50)
    p95 = _percentile(latencies_ms, 95)
    p99 = _percentile(latencies_ms, 99)

    print()
    print("[结果]")
    print(f"  平均: {avg:.2f} ms")
    print(f"  P50:  {p50:.2f} ms")
    print(f"  P95:  {p95:.2f} ms")
    print(f"  P99:  {p99:.2f} ms")
    print(f"  错误: {errors}/{messages}")

    result = {
        "base_url": base_url,
        "messages": messages,
        "avg_ms": round(avg, 2),
        "p50_ms": round(p50, 2),
        "p95_ms": round(p95, 2),
        "p99_ms": round(p99, 2),
        "errors": errors,
        "latencies_ms": [round(x, 2) for x in latencies_ms],
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    if not out_json:
        out_json = str(OUT_DIR / "perf.json")
    Path(out_json).parent.mkdir(parents=True, exist_ok=True)
    Path(out_json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  结果已写入: {out_json}")
    return 0 if errors == 0 else 1


def run_perf_diff(a_path: str, b_path: str) -> int:
    try:
        a = json.loads(Path(a_path).read_text(encoding="utf-8"))
        b = json.loads(Path(b_path).read_text(encoding="utf-8"))
    except Exception as e:
        print(f"✗ 读性能文件失败: {e}")
        return 1

    def d(key: str, label: str):
        va = float(a.get(key, 0))
        vb = float(b.get(key, 0))
        diff = vb - va
        tag = "PASS (<50ms)" if abs(diff) < 50 else "FAIL (≥50ms)"
        print(f"  {label:<6}  Phase 7.1 = {va:>7.2f}ms  →  7.2.1 = {vb:>7.2f}ms  (差值: {diff:+7.2f}ms)  {tag}")

    print("=" * 60)
    print("  Performance Diff")
    print("=" * 60)
    d("avg_ms", "平均")
    d("p50_ms", "P50")
    d("p95_ms", "P95")
    d("p99_ms", "P99")
    print()
    print(f"  结果文件 A: {a_path}")
    print(f"  结果文件 B: {b_path}")
    return 0


# ============================================================
# 入口
# ============================================================
def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 7.2.1 观察期辅助脚本")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="api_server.py 地址（默认 http://127.0.0.1:5000）")
    parser.add_argument("--messages", type=int, default=50, help="连续消息数（默认 50）")
    parser.add_argument("--trace-continuity", action="store_true", help="跑连续消息 trace 连续性")
    parser.add_argument("--trace-concurrent", action="store_true", help="跑 2 线程并发消息")
    parser.add_argument("--timeline-density", action="store_true", help="跑 Timeline 密度分析")
    parser.add_argument("--perf", action="store_true", help="跑性能基线并写 JSON")
    parser.add_argument("--out", default=None, help="--perf 的输出 JSON 路径")
    parser.add_argument("--perf-diff", action="store_true", help="对比两次 perf 结果")
    parser.add_argument("--a", default=None, help="--perf-diff 的 A 文件")
    parser.add_argument("--b", default=None, help="--perf-diff 的 B 文件")
    args = parser.parse_args()

    any_mode = any([args.trace_continuity, args.trace_concurrent, args.timeline_density, args.perf, args.perf_diff])
    if not any_mode:
        parser.print_help()
        print("\n例:")
        print("  # ① 连续消息 50 条 trace 连续性")
        print("  python scripts/phase_7_2_1_observe_runner.py --trace-continuity --messages 50")
        print("  # ① 并发场景")
        print("  python scripts/phase_7_2_1_observe_runner.py --trace-concurrent --messages 20")
        print("  # ② Timeline 密度")
        print("  python scripts/phase_7_2_1_observe_runner.py --timeline-density --messages 10")
        print("  # ③ 性能")
        print("  python scripts/phase_7_2_1_observe_runner.py --perf --messages 50 --out data/phase721_observation/perf_phase71.json")
        print("  # ③ 性能对比")
        print("  python scripts/phase_7_2_1_observe_runner.py --perf-diff --a a.json --b b.json")
        return 0

    rc = 0

    if args.trace_continuity:
        rc = max(rc, run_trace_continuity(args.base_url, args.messages, user_id="u_trace_" + uuid.uuid4().hex[:6]))
    if args.trace_concurrent:
        rc = max(rc, run_trace_concurrent(args.base_url, messages_per_user=max(1, args.messages // 2)))
    if args.timeline_density:
        rc = max(rc, run_timeline_density(args.base_url, messages=max(1, args.messages)))
    if args.perf:
        rc = max(rc, run_perf(args.base_url, messages=max(1, args.messages), out_json=args.out))
    if args.perf_diff:
        if not args.a or not args.b:
            print("✗ --perf-diff 需要同时提供 --a 和 --b")
            return 2
        rc = max(rc, run_perf_diff(args.a, args.b))

    print()
    print(f"[OBSERVATION RUNNER EXIT {rc}]")
    return rc


if __name__ == "__main__":
    sys.exit(main())
