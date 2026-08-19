# Phase 4.1C Memory Continuity · Experience→Memory 全链路审计报告

> 日期：2026-08-12
> 任务：审计 RuntimeExperience → MemoryStore 全链路（按目标卡 5 项检查）
> 状态：**仅审计，未修改任何文件**。审计结论与任务卡的假设前提不一致，需决策后再动手

---

## 1. 五项检查结果

### ① MemoryAdapter 写入格式（`src/runtime/adapters/memory_adapter.py:189-238`）

`_convert_to_memory()` 产出：

```python
{
    "user_id": ...,
    "content": "[RuntimeExperience] {action_type} | trigger=... | success | duration=20ms",
    "role": "system",                               # ← 注意
    "metadata": {"type": "runtime_experience",      # ← 注意
                 "experience_id": ..., "action_type": ..., ...},
}
```

### ② MemoryRecord schema 是否已有 type/source 字段

✅ 有。`metadata.type = "runtime_experience"` 是完整的结构化类型标记，
content 里的 `[RuntimeExperience]` 前缀**作为类型标记确实冗余**——任务卡的假设这一部分成立。

### ③ PollutionGuard 判断依据（`src/memory/pollution_guard.py`）

该记录被**三条相互独立的防线**拦截，缺前缀只解除第一条：

| 防线 | 规则 | 命中 |
|------|------|------|
| content 注入模式 | `INJECTION_PATTERNS` 含 `\[RuntimeExperience\]`（L96） | ✅ 当前报错点 |
| role 黑名单 | `FORBIDDEN_ROLES` 含 `system`（L82）；且 role 仅允许 user/human | ✅ 仍拦截 |
| type 硬黑名单 | `FORBIDDEN_TYPES` 首位即 `runtime_experience`（L37）；白名单仅 11 个 `user_*` 类型 | ✅ 仍拦截 |

**设计意图明确**：`pollution_guard.py` 全文由 commit `ad20c6e`（2026-08-07）新增，
docstring 写明「拒绝以下类型的污染数据: runtime_experience (runtime 内部经验)」。
即 Phase C.2.3 的决策是：**memory.json 只放面向用户的记忆，runtime 内部经验视为污染**。
`MemoryStore.add()` 对一切写入强制过 guard（memory_store.py:196-205），无旁路。

### ④ GrowthPipeline 是否依赖前缀文本

❌ 不依赖。`get_recent_experiences`/`search_experiences` 按
`metadata.type == "runtime_experience"` 过滤（memory_adapter.py:100,139）；
Growth 主路径直接用内存缓存的 experience 对象
（`runtime_growth_pipeline.py:599-601`，`run.metadata["_experience"]`），
持久化 readback 只是 fallback（L609）。全库 src/ 与 tests/ 中无任何代码
解析 content 前缀（除 guard 自身的拒绝测试）。

### ⑤ 历史数据兼容

✅ 无负担。`data/memory.json` 中 `RuntimeExperience` 历史条目为 **0 条**
（8-07 起写入即被全量拦截，从未有记录落盘）。

## 2. 核心结论（与任务卡假设的关键偏差）

任务卡假设：「前缀只是冗余标记 → 移除前缀即可写入」。

审计事实：**前缀确实冗余，但移除前缀无法通过 guard**——role=system 与
type=runtime_experience 仍被第 5、7 条规则硬拒绝。要让经验进入 memory.json，
只剩两条路，且都被您的约束封死：

- 把 role 改为 `user`、type 改为 `user_*` 白名单 → **伪造来源**，
  违背「数据类型由结构字段表达」原则，也把内部动作记录灌入人格记忆池
  （正是 guard 要防的污染）；
- 给 guard 开豁免后门 → 您已明确否决（标签依赖是危险方向）。

**因此验收项「RuntimeExperience 可以成功写入 MemoryStore」在现行约束下不可达，
需要重新取舍。**

## 3. 修正后的最小修复建议（方案甲：Runtime 经验日志）

经验本就不该进用户记忆池（C.2.3 的判断是对的）——断的是**持久化**，
不是**归属**。最小修复：

```
Experience
   ↓
MemoryAdapter.store_experience（接口不变）
   ↓
data/experience_journal.json   ← Runtime 自有经验日志（新文件，非 Memory 系统）
   ↓
get_recent_experiences / search_experiences（接口不变，改读 journal）
   ↓
GrowthPipeline readback ✅
```

- 改动面：仅 `memory_adapter.py` 内部（store_experience / get_recent_experiences /
  search_experiences 的读写目标），接口签名与返回类型不变；
  消费方（runtime_core:1486/1507、growth pipeline、verifier）零改动。
- 不碰 PollutionGuard、不碰 Memory 架构、不新增 Memory 类型。
- 顺带回流修复 2 个既有失败测试（断言语义从「写入 memory.json」
  更新为「经验落盘 journal」）。
- 附带收益：guard 的 `\[RuntimeExperience\]` 内容模式继续有效——
  真正的污染文本（如外部注入伪造的经验标记）仍会被拒。

## 4. 待您拍板

| 选项 | 内容 | 评价 |
|------|------|------|
| **方案甲（推荐）** | 经验改走 Runtime 自有 journal，验收改为「经验可持久化且 Growth 可读取」 | 尊重 C.2.3 意图 + 恢复断点 + 零原则让步 |
| 方案乙 | 伪装 role/type 进 memory.json | 伪造来源，违背原则，不推荐 |
| 方案丙 | guard 豁免 | 您已否决 |

另：本审计发现生产环境 `handle_completed_experience`（runtime_core.py:1265）
每次经验落盘都在 INFO 级静默失败——即「经历过但没留下痕迹」在线上确实发生着，
与您的判断一致，值得尽快修。
