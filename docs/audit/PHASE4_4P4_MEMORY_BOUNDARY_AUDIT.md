# Phase 4.4-P4 — Memory Boundary Audit 记忆边界专项审计

日期：2026-08-12
性质：**只读审计，未修改任何文件**
触发：Phase 4.4-A1 验收后顾问裁决（A1 发现 100 轮模拟 `mem=0`，需确认"经历"与"记忆"是否分区清楚）
禁止项遵守：未改 Memory schema / Growth / Runtime。

---

## 0. 核心结论（先说答案）

**`mem=0` 不是断链，是 Phase 4.1D 的显式架构决策。**

`MemoryAdapter.store_experience()` 的 docstring 明确记载
（memory_adapter.py:13-18，依据 `PHASE4_1C_MEMORY_CONTINUITY_AUDIT.md`）：

> MemoryStore 语义 = 羽依关于用户/关系/事实的长期记忆（PollutionGuard
> 明确拒绝 runtime_experience：type 硬黑名单 + role=system 黑名单）；
> RuntimeExperience = 羽依自己的运行经历，改写 Runtime 自有的
> ExperienceJournal，**MemoryStore 保持纯净**。

4.4-A 模拟直接驱动 `RuntimeCore.process()`，不经过 Orchestrator 聊天外壳，
而 MemoryStore 的**唯一生产写入者在 Orchestrator 路径**——所以模拟中 mem=0
是"测量路径不经过写入者"的产物，不是生产断点。

**但审计确认了三个真实的边界问题**（见 §4）：用户记忆写入入口分裂为二、
Runtime Stage 2 检索能力弱于 Orchestrator、Journal→MemoryStore 无提炼通道。

---

## 1. 三个存储的职责图（现状）

```
┌─────────────────────────────────────────────────────────────────┐
│  MemoryStore（src/memory/memory_store.py，JSON 文件）             │
│  语义：羽依关于【用户/关系/事实】的长期记忆                          │
│  入口守卫：PollutionGuard（memory_store.py:194-208）               │
│    - type 黑名单：runtime_experience 等（pollution_guard.py:37）   │
│    - role 黑名单：system/assistant/tool/function（:175）           │
│  实例持有：RuntimeCore = Memory Authority（get_memory_store:3216） │
│    Orchestrator 经 RuntimeBridge 共享，fallback MemoryProvider 单例 │
├─────────────────────────────────────────────────────────────────┤
│  VectorMemory（src/memory/vector.py，ChromaDB data/chroma_db）    │
│  语义：MemoryStore 的【语义检索索引】——派生物，不是独立事实源         │
│  持久化：PersistentClient 写盘；可从 MemoryStore 全量重建           │
│    （index_memories / _check_and_rebuild）                        │
│  实例持有：RuntimeCore lazy Authority（get_vector_memory:3248）    │
├─────────────────────────────────────────────────────────────────┤
│  ExperienceJournal（src/runtime/experience_journal.py，JSONL）     │
│  语义：羽依【自己的运行经历】（发生过什么）——append-only              │
│  无守卫（内部数据，不经 PollutionGuard）                            │
│  实例持有：MemoryAdapter（RuntimeCore 创建，runtime_core.py:377）   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 写入路径图

### 2.1 MemoryStore —— 全代码库仅 3 个写入者

| # | 写入者 | 路径性质 | 写入内容 | 同步 Vector |
|---|---|---|---|---|
| W1 | `orchestrator.py:876`（Step 10） | **生产聊天主路径** | 仅 user_message 原话（规整后）；role=user；memory_type=`user_shared` | ✅ dual-write（:880） |
| W2 | `interaction_recorder.py:109`（R2.4.1） | RuntimePipeline 路径 | user_message + metadata.reply（截断 500）；memory_type=`user_experience` | ❌ 不写 |
| W3 | `memory_system.py:161` | MemorySystem 服务层 | 服务调用方决定 | 经 MemorySystem.vector |

### 2.2 ExperienceJournal —— 唯一写入者

```
RuntimeCore.handle_completed_experience(store_to_memory=True)   ← 统一入口
  └→ MemoryAdapter.store_experience()                           ← 唯一写入者
       └→ ExperienceJournal.append()   （4.1D：名字带 store_to_memory，
                                          但实际只写 journal + 发领域事件）
```

注意：`handle_completed_experience` 的参数名 `store_to_memory=True` 与
`memory_written` 返回值是 **4.1D 之前遗留的命名**——当前语义是"写入长期系统
（=Journal）"，不是写入 MemoryStore。命名有误导性，但行为正确。

### 2.3 VectorMemory —— 派生写入

- 实时：仅 W1 dual-write（`orchestrator.py:880`）
- 重建：启动时 `_check_and_rebuild` 从 MemoryStore 全量索引
- **W2 写入的记忆不进实时索引**（要等重建才会被语义检索命中）

---

## 3. 读取路径图

### 3.1 Response 的记忆来源（Q3）

**生产主路径（Orchestrator，`orchestrator.py:754-767`）：**

```
memory_store.get_by_user(target_user_id)     ← 全量该用户记忆（无条数上限）
  + vector_memory.search(query, top_k=5)     ← 语义增强，合并去重
  = chat_memories → assemble_context → prompt
```

**Runtime 原生路径（Stage 2，`runtime_core.py:4412-4471`）：**

```
memory_adapter.retrieve()?  ← MemoryAdapter 没有 retrieve() 方法，永远跳过
  ↓ 降级
memory_store.get_by_user(uid)[-20:]          ← 纯时间序最近 20 条，无语义检索
  = ctx.retrieved_memories
  → Stage 14：engine fallback 的 chat_memories（:5472）/ ResponseAdapter
```

**ExperienceJournal 的读取者（与 Response 无关）：**

| 读取者 | 用途 |
|---|---|
| Stage 4 `_collect_growth_candidate_experiences`（:4902） | 投影用户真实文本 → Growth 证据 |
| `memory_adapter.get_recent/search_experiences` | 经验查询接口 |
| self-reflection（经 experience_builder buffer） | 反思洞察 |

**结论：Response 完全不读 ExperienceJournal。**
"我们昨天讨论过什么"这类召回依赖 MemoryStore 里的用户原话记录（W1）。

### 3.2 记忆在回复中的比例（现状）

- 基础盘：MemoryStore 该用户**全部**记忆（Orchestrator 路径无截断，长期会膨胀）
- 增强：VectorMemory top-5 语义相关
- Runtime 原生路径退化为基础盘的最近 20 条子集
- Journal / Relationship / SelfModel 不直接作为"记忆"进入回复（分别走
  Growth / relationship_context / self_model_context 独立通道）

---

## 4. 重复 / 缺失点（审计发现）

### F1（中）：用户记忆写入入口分裂为二，行为不一致

W1（Orchestrator）与 W2（InteractionRecorder）都能写 MemoryStore：

| 维度 | W1 Orchestrator | W2 InteractionRecorder |
|---|---|---|
| memory_type | `user_shared` | `user_experience` |
| 是否存羽依回复 | 否 | 是（metadata.reply 截断 500） |
| Vector 实时同步 | ✅ | ❌ |
| user_id 来源 | target_user_id（resolver 修正） | ctx.inputs 或硬编码默认 `"366648462"` |

后果：同一用户的记忆可能有两种 type 标记；W2 写入的记忆实时语义检索不可见；
W2 的硬编码默认 user_id 有串户风险（有 fallback 但值得注意）。
这正是顾问警告的"两个都半管"。

### F2（中）：Runtime Stage 2 检索能力弱于 Orchestrator 路径

Stage 2 的优先路径要求 adapter 有 `retrieve(query, user_id)`，但 RuntimeCore
装配的是**经历导向的 MemoryAdapter（无 retrieve）**，不是检索导向的
MemoryRuntimeAdapter（有 retrieve + vector fallback，但未接入 Stage 2）。
结果：作为生命周期中心的 RuntimeCore，自己的记忆检索只有"最近 20 条"，
向量能力（get_vector_memory Authority 已存在）完全没有服务于 Stage 2。

### F3（设计空白，Q2）：Journal → MemoryStore 无提炼通道

当前不存在的链路：

```
ExperienceJournal（"用户多次深入讨论 AI 架构"）
      ↓ 提炼/评估
UserPreference / LongTermFact
      ↓
MemoryStore（"用户正在开发羽依 AI 项目"）
```

最接近的既有通道是 journal → Growth → proposal →（审批后）Personality/SelfModel，
但提炼结果落在人格侧，**不回 MemoryStore**。MemoryStore 的内容永远只是
用户消息原话（W1）或原话+回复（W2）。这是设计空白而非缺陷——
是否建立该通道是架构决策，需顾问裁决。

### F4（低，观测项）：`handle_completed_experience` 参数名误导

`store_to_memory=True` / `memory_written` 是 4.1D 前命名，实际写 Journal。
不影响行为，但对后续维护者是断点假象的来源之一。

### F5（低，观测项）：VectorMemory 内存实例多点自建

5 处 fallback `VectorMemory()` 自建（orchestrator:141、memory_system:65、
memory_service:51、context_manager:71、memory_runtime_adapter:320）。
底层共享同一 ChromaDB 持久目录，数据一致性无虞；但"Authority 单例"意图
与"到处 fallback 自建"现实之间存在张力，长期应收敛。

---

## 5. Q1 / Q2 / Q3 回答

### Q1：谁拥有用户长期记忆？

- **事实源**：MemoryStore（唯一，VectorMemory 是其派生索引）
- **实例持有（Authority）**：RuntimeCore ✅ 已统一（Phase 4.3.1）
- **写入责任**：❌ 分裂 —— 实际是 Orchestrator（W1 主）+ InteractionRecorder（W2 辅），
  RuntimeCore 持有实例但不写用户记忆
- **结论**：不是方案 A 也不是方案 B，是"A 外壳写入 + B 持有实例"的混合态。
  写入责任需要收敛（见 §6 推荐）

### Q2：ExperienceJournal 是否需要反哺 Memory？

- 现状：**没有反哺通道，也没有反哺需求导致的故障**——
  因为 W1 已经把用户原话存进 MemoryStore，"昨天讨论过什么"可以召回
- 真正的空白是**提炼层**：MemoryStore 只有原话，没有"羽依理解后的事实/偏好"
- 顾问设想的 `经历 → Evaluator → UserPreference → MemoryStore` 是合理方向，
  但属于新机制，不建议在 4.4 阶段建立（见 §6）

### Q3：Response 检索来源是什么？

| 来源 | 生产主路径 | Runtime 原生路径 |
|---|---|---|
| MemoryStore | ✅ 全量 by-user | ✅ 最近 20 条 |
| VectorMemory | ✅ top-5 | ❌ |
| ExperienceJournal | ❌ | ❌ |
| Relationship / SelfModel / Emotion | 独立上下文通道（非"记忆"） | 同左 |

长期连续性**成立**（W1 持久化用户原话 + vector 语义召回 + 跨重启恢复），
但 Runtime 原生路径的召回质量弱一档（F2）。

---

## 6. 推荐边界（供顾问裁决，本审计不实施）

### 推荐：方案 A'（收敛写入 + 补齐 Stage 2，最小改动）

保持 4.1D 三存储语义不动（经历/记忆分离是正确决策），只做两个收敛：

1. **统一用户记忆写入入口**（修 F1）：
   明确 W1 Orchestrator 路径为唯一生产写入者；W2 InteractionRecorder
   或对齐 W1（统一 memory_type、补 vector 同步、修硬编码 user_id）、
   或标记 legacy 禁用其 MemoryStore 写入（保留其事件发布）。
2. **Stage 2 接入既有向量能力**（修 F2）：
   复用 `get_vector_memory()` Authority，在 Stage 2 降级路径中增加
   vector search 合并（不新建检索系统；MemoryRuntimeAdapter 已有
   retrieve+vector fallback 实现，可评估接入或直接内联最小逻辑）。

### 暂缓：方案 B（Journal→Memory 提炼通道）

F3 是真实空白，但涉及"什么经历值得变成长期事实"的判断设计
（与 GrowthEvaluator 的关系、PollutionGuard 的放行标准），
建议等 4.4-B 冲突成长测试验证 Growth 管线稳定后再专项设计。

### 顺手项（可与下次测试维护合并）

- F4：`store_to_memory` 参数/注释更名（如 `persist_experience`），消除断点假象
- F5：VectorMemory fallback 自建点收敛为 Authority 单例（测试基建性质）

---

## 7. 对 4.4-B/C 的影响评估

顾问暂停 4.4-B/C 等待本审计的结论是正确的，但审计结果显示：

- **4.4-B（冲突成长）不受影响**：冲突成长链路走的是
  journal → Growth → proposal，与 MemoryStore 边界问题（F1/F2/F3）无交集。
  A1 修复后该链路已在 100 轮尺度验证（proposal 9/9 pending）。
- **4.4-C（恢复测试）部分相关**：恢复测试需覆盖 MemoryStore/VectorMemory/
  Journal 三存储的跨重启一致性，F1（双写入者）若先收敛，恢复语义会更清晰；
  但不阻塞——按现状测出的恢复行为也是有效基线。

**建议顺序**：4.4-B/C 可以恢复（与 F1/F2 修复并行不悖），
或按顾问偏好先收敛 F1/F2 再测。两条路都安全。

---

## 8. 审计范围与方法声明

- 全程只读：Grep/Read 源码 + 既有 4.4-A 快照数据，未运行任何写入型测试，
  未修改任何文件
- 关键证据锚点：memory_adapter.py:13-18（4.1D 决策记载）、
  memory_store.py:194-208（PollutionGuard 入口）、orchestrator.py:754-880
  （检索+写入主路径）、runtime_core.py:4412-4471（Stage 2）、
  runtime_core.py:4902-4953（Stage 4 journal 读取）、
  interaction_recorder.py:90-109（W2）、vector.py:15-117（Chroma 持久化+重建）
