# Phase 4.1D Experience Persistence 修复 · 架构影响报告

> 日期：2026-08-12
> 前置：Phase 4.1C 审计（PHASE4_1C_MEMORY_CONTINUITY_AUDIT.md）确认
> Experience→MemoryStore 入口被 PollutionGuard 三轴拦截（C.2.3 故意设计），
> 经验持久化自 2026-08-07 起 100% 静默失败——「经历过但没留下痕迹」
> 方案：批准的方案甲——经验独立持久化，不进入 MemoryStore

---

## 1. 修复内容

### 新增 `src/runtime/experience_journal.py`（唯一新文件）

`ExperienceJournal`：append-only JSONL 经验日志，复用项目既有持久化模式
（与 ActionPersistenceManager / EvolutionHistoryStore 同款约定）：

- 仅 stdlib；线程锁；append-only（从不 truncate）
- fail-soft：写盘失败降级内存模式（本进程仍可读），不抛异常
- 损坏行隔离：跳过 + 计入 `corrupt_count`
- 容量保护：>64MB 单轮转（`.1` 后缀）
- 不 import src.memory.* —— 经验不属于 Memory 系统

默认路径 `data/experience_journal.jsonl`，可由 config
`experience_journal_path` 覆盖；未配置时派生为 memory_store 同目录。

### 修改 `src/runtime/adapters/memory_adapter.py`（接线，接口不变）

| 方法 | 变更 |
|------|------|
| `__init__` | 新增可选 `journal_path`；创建 ExperienceJournal |
| `store_experience` | 改写 journal（原写 MemoryStore 被 guard 拒绝）；补顶层 `id`/`timestamp` 盖戳（与 MemoryStore 过去行为对齐） |
| `get_recent_experiences` / `search_experiences` | 改从 journal 读取；过滤/排序/反序列化逻辑不变 |
| `_convert_to_memory` | **移除 `[RuntimeExperience]` 内容前缀**——类型由 `metadata.type` 结构字段表达；guard 的前缀模式保留，继续拦截注入伪造 |

接口签名与返回类型全部不变，5 处调用方
（experience_builder:210 / growth_pipeline:266 / runtime_core:997,1267 /
cognitive_loop_verifier:95）零改动。

### 修改 `src/runtime/runtime_core.py`（1 行）

MemoryAdapter 构造传入 `journal_path=self.config.get("experience_journal_path")`。

### 未触碰

- `src/memory/pollution_guard.py` —— 规则零变化（git diff 为空）
- Memory 架构 / Growth / SelfModel 接口 —— 零变化
- 无新增 Memory 类型

## 2. 修复后的数据流

```
                 ┌──────────────┐
                 │ User Memory  │  用户/关系/事实（11 种 user_* 白名单）
                 └──────┬───────┘
                    MemoryStore  ← PollutionGuard 守护（规则不变）
                        ↓
                 Prompt / 对话召回

 用户经历
    ↓
 Runtime Experience
    ↓
 ExperienceJournal（data/experience_journal.jsonl）  ← 本次修复
    ↓
 Growth / Reflection / SelfModel（readback 接口不变）
```

## 3. 验收结果

### 新增 `tests/runtime/test_experience_journal.py`（13 个测试全过）

| 验收标准 | 测试 | 结果 |
|----------|------|------|
| experience 跨重启恢复 | test_cross_restart_recovery（新实例读同一 journal） | ✅ |
| Growth 可读取历史 experience | test_growth_can_read_persisted_experiences（runtime 接口） | ✅ |
| MemoryStore 不出现 runtime_experience | test_memory_store_stays_clean + e2e 断言 | ✅ |
| PollutionGuard 规则不变 | 前缀内容仍拒 / runtime_experience 类型仍拒 / 用户记忆仍放行 | ✅ |
| journal 健壮性 | 往返 / 损坏行隔离 / 写盘失败降级 | ✅ |
| 无内容前缀 | test_content_has_no_runtime_prefix | ✅ |

### 既有测试语义更新（任务卡批准）

- `test_end_to_end_simulation.py`：步骤 3 改为「经验落 journal + MemoryStore
  保持纯净」断言；步骤 4 相关性排序改为对用户记忆（符合新架构语义）→ **通过**
- `test_cognitive_loop_verifier.py`：**无需改动即通过**（store_experience 恢复
  返回 True，reflection 走 buffer 不受影响）——两个原本质上就是被断点拖累

### 全量套件

```
pytest tests/runtime  →  159 passed, 2 failed
```

修复前 6 failed → 修复后 2 failed。剩余 2 个均为 committed HEAD 既有问题，
与 4.1x 全部工作无关（stash 基线对比已证）：

1. `test_fallback_when_engine_missing_no_exception` —— ResponseAdapterImpl
   忽略显式 fallback_reply 配置改回人格化兜底（需确认有意/bug）
2. `test_runtime_records_relationship_interaction` ——
   `record_relationship_interaction` 返回 None（正对应您优先级 P1
   「Relationship 接入 Runtime」的现成切入点）

「SelfModel chain 可追踪来源」验收项：由 4.1b/4.1C 既有
`ctx.self_model_chain`（evolution→validation→persistence 逐段标记 +
suggestion 的 source_type/source_id）覆盖，本轮无需新增。

## 4. 架构影响（对照判断标准）

| 标准 | 增益 |
|------|------|
| 记忆连续性 | **断点修复**：经验不再丢失，跨重启可恢复；记忆池语义分层（User Memory / Experience Journal / Self History）正式确立 |
| 成长连续性 | Growth/Reflection 的 readback fallback 恢复真实数据源 |
| 长期稳定性 | journal append-only + 轮转 + 损坏隔离 + fail-soft，与既有持久化件同标准 |
| 安全语义 | guard 三轴规则零让步；`[RuntimeExperience]` 模式继续拦截注入伪造 |

## 5. 遗留事项

1. 服务器侧补跑全量 pytest（本机用户 Python 3.14.6 + pytest 9.1.1 已全量验证）。
2. 既有失败 ×2 待决策（见 §3），其中 relationship 失败即 P1 切入点。
3. journal 轮转只保留一代（`.1`），长期运行如需归档可后续扩展（非本轮范围）。
4. `_convert_from_memory` 反序列化不含 trigger_event/self_state（历史如此）；
   Growth 主路径用内存缓存对象不受影响，跨重启 replay 的保真度可后续增强。
