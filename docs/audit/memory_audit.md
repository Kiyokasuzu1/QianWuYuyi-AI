# Memory Audit Report

**审计阶段：** Phase A.3.1
**审计范围：** `src/memory/` + `data/memory.json` + MemoryProcessor / memory_reflection
**审计目标：** 验证长期记忆质量是否满足"人格连续性"长期设计目标
**审计原则：** 不重构、不破坏现有功能；只识别问题、给出建议、必要时小修复

---

## 1. 当前数据流概览

```
用户消息
  ↓
Orchestrator.process()
  ├→ MemoryExtractor (rule-based, 只读 user role)
  │    ↓
  │  MemoryCandidate (preference / identity / event / relationship / emotion_candidate)
  │    ↓
  │  MemoryVerifier (CLASS_*, truth, risk, usage 授权)
  │    ↓
  │  memory_store.add() → data/memory.json
  │
  └→ MemoryConsolidationEngine (周期性, 不删除原始记忆)
       ↓
     ConsolidatedMemory (episodic / semantic / identity / relationship / emotional)
```

**关键发现：** MemoryExtractor 是**纯规则提取器**（v1.3.0），不是 LLM 提取器。第 244 行 `if msg.get("role") != "user": continue` 严格只处理用户消息。

---

## 2. 风险识别（按风险等级排序）

### 🔴 高风险（High Risk）

#### H1. `data/memory.json` 包含原始 LLM 提示词和 system_reminder 污染

**位置：** `data/memory.json` 多条 memory 的 `content` 字段
**问题描述：** 由于记忆提取发生在 RAG-Faiss-Memory 注入之前，部分 `content` 字段被混入：

- `<extra_instruction>Based on our full conversation history, produce a concise summary...</extra_instruction>`
- `<system_reminder>User ID: 366648462, Nickname: 清夏铃...</system_reminder>`
- `<RAG-Faiss-Memory>--- BEGIN HISTORICAL MEMORY REFERENCE ---...</RAG-Faiss-Memory>`

**风险：** 这些内容会进入长期记忆，污染人格上下文，LLM 在引用记忆时会"看到"自己的 system prompt 片段。

**风险等级：** 🔴 高（数据污染已存在）

**修复建议：**
- 短期：在 `MemoryExtractor.extract()` 之前增加 `_strip_system_artifacts(text)` 预处理
- 中期：把"清理污染内容"做成 verifier 的硬性条件（class = `source_document` 但 content 含 `<extra_instruction>` → 拒绝）

**是否需要代码修改：** 需要（建议小修复，但**不在 Phase A.3 范围内**——只标记）

---

#### H2. `data/memory.json` 中"重要性 0.95"的 RAG 自动注入记忆由 LLM 写成长叙事

**位置：** `data/memory.json` 中 5 条 Importance: 0.95 的记忆
**问题描述：** 这些记忆的 content 字段包含：

```
"清清在 2026-07-24 凌晨零点又启动'小黑猫全自动敲代码'，早上六点给我看了一份超完整的总结..."
"我说：'三个累才能表达他真实的疲劳程度。'"
```

**问题分析：**

| 维度 | 现状 | 风险 |
|---|---|---|
| 事实性 | 包含用户原话和行为 | ✅ 可追溯 |
| 叙事视角 | 第一人称（"我说"、"我关心"） | ⚠️ AI 视角 |
| 概括程度 | 高度概括性陈述 | ⚠️ 接近 personality trait |
| 时态 | 混合（"昨晚"、"凌晨"） | ⚠️ 容易变成"事实" |
| 推断 | "我知道他经历过失去" | 🔴 推断当成事实 |

**对比用户期望的"避免污染"：**

> ❌ 不推荐：`"清夏铃永远会守护羽依"`
> ✅ 推荐：`"清夏铃在羽依状态异常期间持续确认羽依状态"`

当前 0.95 重要性的记忆**介于两者之间**——记录了事实但带第一人称叙述和"我知道他经历过失去"这种推断。

**风险等级：** 🔴 高（叙事污染存在）

**修复建议：**
- 短期：在 memory_extractor 提取时，把 "我知道"、"我猜"、"我觉得" 标记为 `assistant_output`（truth=0.0）类
- 中期：增加 `MemoryVerdict.SPECULATIVE` 分类，所有第一人称推断强制打标
- 长期：把"长期人格特征"提取从 MemoryExtractor 拆分到独立的人格特征提取器（避免污染）

**是否需要代码修改：** 需要（**不实现**，仅标记；当前阶段 MemoryExtractor 是规则提取，0.95 重要性来自外部 RAG 注入而非本系统提取）

---

### 🟡 中风险（Medium Risk）

#### M1. 普通聊天可能直接被保存为"事实"而未做事件类型区分

**位置：** `MemoryExtractor._try_event()`、`MemoryVerifier.CLASS_EVENT` (truth=0.8)
**问题描述：** `_try_event` 模式匹配后直接生成 `CLASS_EVENT` 候选，没有要求"必须经过多轮对话确认"或"事件必须包含时间地点"。

**当前规则（memory_extractor.py）：**
```python
EVENT_PATTERNS = [
    (r"今天(.*?)了", 0.6),  # 任意"今天xxx了"都被当作事件
    ...
]
```

**风险：** 单次聊天中的"今天我吃饭了"会被提取为 EVENT 类记忆（truth=0.8），并在后续的 consolidation 阶段被强化。

**修复建议：**
- 提高 EVENT 提取的最低 confidence 阈值（从 0.6 提升到 0.75）
- 在 verifier 阶段对"一次事件"做"是否长期人格特征"的检查

**是否需要代码修改：** 可选（当前 EVENT 提取 confidence 已经偏低，0.6 起步；建议在 Phase A.4 处理）

---

#### M2. MemoryConsolidationEngine 的"反复强化"机制可能让单次事件变成长期人格

**位置：** `src/memory/memory_consolidation_engine.py` 第 61 行
**问题描述：**
```python
truth=round(min(1.0, truth + min(0.2, 0.03 * (reinforcement - 1))), 4)
```
当同一个 canonical_key 反复出现，truth 会线性累加。**问题：**
- 如果 `canonical_key` 是 "用户在情绪低谷时总会找我" 这种概括性总结
- 反复 5-10 次后，truth 接近 1.0
- 后续 LLM 会认为这是"铁的事实"

**风险：** 长期人格特征被无意识强化。

**修复建议：**
- 在 consolidation 阶段增加"是否单次事件的归一化"——对 reinforcement_count > 5 的记忆打标 "needs_review"
- 在 SelfModel 注入时，对 `needs_review=true` 的记忆降低权重

**是否需要代码修改：** 可选（建议在 Phase A.4 处理）

---

#### M3. Memory 与 Growth 的边界依赖 Verifier 自觉，可能绕过

**位置：** `memory_verifier.py` 第 53 行
```python
CLASS_ASSISTANT_OUTPUT: 0.0,  # truth = 0.0
```
**问题描述：** 虽然 `CLASS_ASSISTANT_OUTPUT` 的 truth 是 0.0，但 `DEFAULT_USAGE` 仍然包含 `["reference"]`：
```python
CLASS_ASSISTANT_OUTPUT: ["reference"],
```

这意味着 AI 自己说的话可以"作为 reference 被使用"。

**风险等级：** 🟡 中（影响有限但有漏洞）

**修复建议：**
- 把 `CLASS_ASSISTANT_OUTPUT` 的 usage 改为 `[]`（不允许任何用途）
- 或者严格只允许在 SelfModel Audit 报告中作为 reference

**是否需要代码修改：** 可选（建议在 Phase A.4 处理）

---

### 🟢 低风险（Low Risk）

#### L1. 偏好提取的 confidence 在 0.65-0.75 区间

**位置：** `memory_extractor.py` 第 48-69 行
**评估：** 阈值合理，不会过度强化。

#### L2. MemoryExtractor 已强制只读 user role

**位置：** `memory_extractor.py` 第 244 行
**评估：** ✅ 严格隔离 AI 自己的输出，不会把"羽依说：你真好"作为用户事实保存。

#### L3. MemoryConsolidationEngine 不删除原始记忆

**位置：** `memory_consolidation_engine.py` 第 35 行
**评估：** ✅ 不破坏 data/memory.json，生成视图而非修改源数据。

---

## 3. 与用户期望的对照

| 用户期望 | 现状 | 是否满足 |
|---|---|---|
| LLM 不创造不存在的事实 | ⚠️ 部分满足（Extractor 规则安全，但 RAG 注入的 0.95 记忆包含第一人称推断） | 部分 |
| 不将推测写成事实 | ⚠️ 部分满足（truth=0.0 的 assistant_output 会被 reference 使用） | 部分 |
| 不将 AI 自己的回复作为用户事实保存 | ✅ 满足（`if msg.get("role") != "user": continue`） | 满足 |
| 避免过度情绪化叙事污染长期记忆 | ⚠️ 部分满足（0.95 重要性记忆含第一人称情绪叙事） | 部分 |
| 不将一次事件错误提升为长期人格特征 | ⚠️ 部分满足（consolidation 强化机制可能放大） | 部分 |

---

## 4. 修复建议优先级

| 优先级 | 修复项 | 预计影响 | 是否在 A.3 范围 |
|---|---|---|---|
| P0 | 增加 memory_audit 健康检查（不修改任何业务代码） | 高 | ✅ 是 |
| P1 | 在 MemoryExtractor 入口前增加 `<extra_instruction>` 等 artifact 过滤 | 高 | ❌ 否（待 Phase A.4） |
| P1 | 把 CLASS_ASSISTANT_OUTPUT 的 usage 改为 `[]` | 中 | ❌ 否（待 Phase A.4） |
| P2 | 提高 EVENT 提取 confidence 阈值 | 中 | ❌ 否 |
| P2 | consolidation 阶段对 reinforcement > 5 的记忆打 `needs_review` 标 | 中 | ❌ 否 |

**Phase A.3 范围：** 仅创建 `tests/test_memory_health.py` 做健康检查（不修改任何业务代码）。

---

## 5. 结论

| 维度 | 评估 |
|---|---|
| 是否会污染长期记忆 | **是**（已存在的 0.95 RAG 注入记忆含第一人称推断） |
| 是否能让 LLM 看到不存在的事实 | **部分**（取决于 RAG 注入的内容，已发生） |
| 是否需要立即修复 | **否**（数据已存在，但当前 LLM 调用时不会"主动制造"事实） |
| 是否需要 Phase A.3 修改代码 | **否**（仅创建健康检查测试） |

**Phase A.3 仅做的事：**
1. ✅ 本审计报告
2. ✅ `tests/test_memory_health.py` 健康检查测试
3. ❌ 不修改任何业务代码（避免破坏现有功能）

---

## 附录 A：当前 memory.json 统计

| 项目 | 数量 |
|---|---|
| 总记忆数 | 25+（部分为 RAG 注入） |
| 重要性 0.95 记忆 | 5（含 RAG 长叙事） |
| 重要性 0.5 默认 | 20+ |
| 包含 system_reminder | 至少 4 条 |
| 包含 RAG-Faiss-Memory | 至少 3 条 |
| 包含 extra_instruction | 至少 1 条 |

**审计日期：** 2026-08-02
**审计者：** Phase A.3.1 自动审计
