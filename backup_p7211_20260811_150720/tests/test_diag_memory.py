# -*- coding: utf-8 -*-
"""只读诊断: memory/overview 超时 + 数据量检查"""
import urllib.request
import json
import time

BASE = "http://198.44.178.195:5000/api/v1"
TOKEN = "test-token-yuyi-2026"

print("=" * 70)
print("诊断1: /memory/overview 超时 + 数据量")
print("=" * 70)

# 1. 用不同 timeout 测试
for timeout_s in [10, 20, 30, 60]:
    url = BASE + "/memory/overview"
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", "Bearer " + TOKEN)
    t0 = time.monotonic()
    try:
        resp = urllib.request.urlopen(req, timeout=timeout_s)
        elapsed = time.monotonic() - t0
        body = resp.read()
        data = json.loads(body)
        inner = data.get("data", {})
        if isinstance(inner, dict):
            tc = inner.get("total_count")
            ic = inner.get("important_count")
            recent = inner.get("recent", [])
            uid = inner.get("user_id")
            print("timeout=%2ds -> status=%d  elapsed=%.2fs  total_count=%s  important=%s  recent_len=%s  user_id=%s" % (
                timeout_s, resp.status, elapsed, tc, ic, len(recent) if isinstance(recent, list) else "?", uid))
            # 打印 recent 的时间戳
            if isinstance(recent, list) and recent:
                for i, m in enumerate(recent[:5]):
                    if isinstance(m, dict):
                        ts = m.get("timestamp") or m.get("created_at") or m.get("time") or m.get("datetime") or "?"
                        content_preview = str(m.get("content", m.get("text", "")))[:80]
                        print("  recent[%d] ts=%s  content=%s" % (i, ts, content_preview))
        break
    except urllib.error.HTTPError as e:
        elapsed = time.monotonic() - t0
        print("timeout=%2ds -> HTTP %d  elapsed=%.2fs" % (timeout_s, e.code, elapsed))
        break
    except Exception as e:
        elapsed = time.monotonic() - t0
        print("timeout=%2ds -> EXCEPTION  elapsed=%.2fs  %s" % (timeout_s, elapsed, type(e).__name__))

print()
print("=" * 70)
print("诊断4: /personality/evolution 交叉验证记忆时间")
print("=" * 70)

url = BASE + "/personality/evolution?limit=200"
req = urllib.request.Request(url, method="GET")
req.add_header("Authorization", "Bearer " + TOKEN)
try:
    resp = urllib.request.urlopen(req, timeout=15)
    data = json.loads(resp.read())
    inner = data.get("data", {})
    items = inner.get("items", []) if isinstance(inner, dict) else []
    # 找 memory.created 事件
    mem_events = [it for it in items if isinstance(it, dict) and it.get("event_type") == "memory.created"]
    print("total evolution items: %d" % len(items))
    print("memory.created events: %d" % len(mem_events))
    if mem_events:
        print("first memory.created: %s" % mem_events[0].get("timestamp"))
        print("last  memory.created: %s" % mem_events[-1].get("timestamp"))
        print()
        print("Last 5 memory.created events:")
        for ev in mem_events[-5:]:
            ts = ev.get("timestamp")
            print("  %s" % ts)
    # 所有事件按时间排序，看最新5条
    if items:
        print()
        print("Last 5 evolution events (all types):")
        for ev in items[-5:]:
            ts = ev.get("timestamp")
            et = ev.get("event_type")
            print("  %s  %s" % (ts, et))
except Exception as e:
    print("EXCEPTION: %s" % e)

print()
print("=" * 70)
print("诊断4b: /audit/recent 交叉验证")
print("=" * 70)

url = BASE + "/audit/recent?limit=20"
req = urllib.request.Request(url, method="GET")
req.add_header("Authorization", "Bearer " + TOKEN)
try:
    resp = urllib.request.urlopen(req, timeout=10)
    data = json.loads(resp.read())
    inner = data.get("data", {})
    items = inner.get("items", []) if isinstance(inner, dict) else []
    print("audit items: %d" % len(items))
    for item in items[:5]:
        ts = item.get("timestamp") or "?"
        action = item.get("action") or item.get("event_type") or "?"
        print("  %s  %s" % (ts, action))
except Exception as e:
    print("EXCEPTION: %s" % e)
