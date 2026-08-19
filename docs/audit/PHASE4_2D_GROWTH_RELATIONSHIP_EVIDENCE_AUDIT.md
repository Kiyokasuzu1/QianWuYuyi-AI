# Phase 4.2-D — Growth Relationship Evidence 审计报告

日期：2026-08-12
性质：**纯审计，未修改任何代码**

---

## 0. 任务卡约束复述

- 只接线，不新增成长系统
- 禁止：修改 PersonalityResolver / 修改 IDENTITY_CORE / 增加关系权重 / 增加亲密度评分 / Relationship 直接修改人格
- 正确方向：长期互动经历 → 事件证据 → GrowthEvaluator 判断 → 可解释成长提案
- 优先检查 GrowthEvaluator 而非 PersonalityResolver（Relationship 只能提供证据，不能决定变化）

---

## 1. 当前链路（查证结果）

### 1.1 GrowthEvaluator 已完整支持 relationship 事件 —— 评估端不需要改

`src/growth/growth_evaluator.py`（v1.1）+ `src/contracts/growth_schema.py`：

| 资产 | 现状 |
|---|---|
| `event_type` 枚举 | 已含 `"relationship"`（growth_schema.py:18） |
| `growth_domain` 枚举 | 已含 `"relationship_context"`（:35） |
| 领域映射 `_determine_domain` | `relationship → relationship_context`（evaluator:369） |
| 领域层级上限 `DOMAIN_MAX_LEVEL` | `relationship_context → "context"`，永远不能进 preference/trait |
| 硬红线 `_check_growth_allowed` | `event_type=="relationship"` 且 level∈{preference,trait} → 拒绝（:396） |
| 变化量 | relationship 领域 `applied_delta = 0.0` 强制 |
| 目标信号 `_resolve_growth_signal` | relationship 默认返回 `""`（无目标维度，:433） |

**结论：顾问担心的"关系改变人格"红线在评估端已经存在且是硬编码的。4.2-D 不需要也不应该动 evaluator。**

### 1.2 digest 管线契约（从既有测试还原）

`tests/test_growth_digest_baseline_r2_5_0_b.py::_run_digest_pipeline`：

```
raw event（EventExtractor 风格 dict）
  {event, topic, event_type, importance, evidence[{text,role,source_index,memory_id}], source_ids[]}
    ↓ EventNormalizer.normalize([raw])   # 分类 + canonical_topic 稳定化
    ↓ EventValidator.should_keep()       # 过滤 + 最终 importance
    ↓ EventHistoryMatcher.get_history()  # 历史稳定性
    ↓ GrowthEvaluator.evaluate(normalized, history)
```

既有失败测试 case3 正是"关系保护：relationship 只进 context，applied_delta=0"——修复后可作为 4.2-D 的回归证据。

### 1.3 两条关系/成长路径并存，互不连通

**路径 A — Legacy GrowthPipeline（`src/growth/pipeline.py` v0.7）**
- LLM EventExtractor（聊天记录 → LLM 提取事件，prompt 含 relationship 类型）→ normalizer → validator → evaluator → GrowthEngine
- `_update_relationship` 操作的是**另一套** `src.personality.relationship_state.RelationshipState`（5 维 trust/bond/familiarity/promise/history），做 `update_trust(0.05*importance)` 朴素递增 —— 正是顾问反对的好感度模式
- **生产调用方：无**（grep 仅见自身模块引用）

**路径 B — Runtime 路径**
- RuntimeCore Stage 4 GROWTH_EVALUATION 调 `growth_adapter.evaluate(ctx)`
- 但生产装配的 `src/runtime/adapters/growth_adapter.py::GrowthAdapter` **没有 evaluate 方法**（只有 store_insight/list_proposals/accept/reject —— ReflectionInsight→GrowthProposal 审批流实现）→ Stage 4 实际 AttributeError→fail-soft→**no-op 休眠**
- 同文件存在 `GrowthAdapterSpec`（Phase 3.7.1 设计层接口），其 `evaluate()`/`submit()` 均 `raise NotImplementedError`
- `src/runtime/pipeline/runtime_growth_pipeline.py`（Phase 6.0 编排器：Experience→Memory→Reflection→GrowthProposal→Lifecycle 审批→Personality）**仅被测试引用，无生产接线**

### 1.4 Relationship 新体系（src/relationship，R2.5.2-B）→ Growth：零连接

- 4.2-B 的 `record_relationship_interaction` 结果只进 relationship repository + `relationship_changed` 领域事件
- **没有任何消费者**把 RelationshipEvent / shared_experiences 转成 Growth 事件或提案证据
- Relationship extractor 的 `preference_learning` 类型事件（"习惯/偏好/了解/知道你喜欢"关键词）产生的证据（evidence_ids 锚定 experience_id）**正是顾问示例"用户偏好详细技术讨论 → Communication Preference 提案"的现成数据源，但目前无人消费**

---

## 2. 缺失点

| # | 缺失 | 影响 |
|---|---|---|
| G1 | Relationship 新体系事件从未进入 digest 管线 | 关系经历不产生任何成长证据 |
| G2 | Stage 4 evaluate 空转（GrowthAdapter 无该方法，Spec 未实现） | Runtime 生命循环里成长评估环节名存实亡 |
| G3 | preference_learning 事件证据无人消费 | 顾问目标示例（偏好→Communication Preference 提案）无法发生 |
| G4 | 两条 RelationshipState 并存（personality 旧 5 维好感度 vs relationship 新 3 维），旧路径含 trust+0.05 朴素递增 | 架构语义混淆，旧路径与设计理念相悖 |

---

## 3. 可复用资产（按复用价值排序）

1. **GrowthEvaluator relationship 全红线支持**（§1.1）——最重要，evaluator 零改动
2. **digest 管线四阶段**（normalizer/validator/matcher/evaluator）——纯函数式，测试已示范如何独立驱动
3. **`GrowthProposal.evidence_ids`** —— canonical schema 原生支持证据锚定
4. **Relationship `preference_learning` 事件 + evidence_ids**（4.2-B 已锚定 experience_id）——现成证据源
5. **evaluator 的 preference 域通道**：digest `event_type="preference"` → domain `"preference"`，红线只拦截 `event_type=="relationship"` —— 即"关系经历中发现的偏好"可以合法走 preference 域，**只要事件类型如实标注为 preference 而非 relationship**
6. 既有失败测试 case3（关系保护断言）——修复后转绿即回归证据

---

## 4. 最小接线方案（建议，待批准后实施）

### 原则

接入口在 **GrowthEvaluator 上游（Normalizer 之前）**，不动 evaluator 红线、不动 PersonalityResolver、不动 IDENTITY_CORE、不新增系统。

### 方案：Runtime 侧 Relationship→Digest 事件转换器（薄适配层）

```
RelationshipEvent（4.2-B 已发）
    ↓ 【新增薄转换器，仅此一处新代码】
digest raw event（EventExtractor 风格 dict，§1.2 契约）
    ├─ event_type="preference_learning" → "preference"（偏好证据，走 preference 域既有映射）
    └─ 其他 relationship 事件           → "relationship"（→ relationship_context 域，delta=0）
    ↓ EventNormalizer → EventValidator → EventHistoryMatcher
    ↓ GrowthEvaluator.evaluate()（既有红线自动生效）
    ↓ 成长信号 → GrowthProposal（evidence_ids 锚定 experience_id）
    ↓ 既有 approval 审批流（GrowthAdapter.store_insight / accept / reject）
```

关键设计点：

1. **类型如实标注**：偏好类证据标 `preference`（合法进入 preference 域，受 evaluator 阈值与 approval 双重门控）；关系本体标 `relationship`（硬限制 context 层，delta=0）。不存在"开后门"。
2. **证据可追踪**：每条 digest event 的 `evidence[].memory_id` / `source_ids` 直接复用 RelationshipEvent 的 experience_id 锚点。
3. **不激活 Stage 4**：本方案不改变 Stage 4 休眠现状；digest 驱动点放在 Relationship 事件消费侧。Stage 4 的 GrowthAdapterSpec 实现属另一阶段的事，不在 4.2-D 范围。
4. **旧路径不动**：legacy GrowthPipeline 与 personality 旧 5 维 RelationshipState 本次不修改（其退役属已冻结的独立议题），仅在报告中标注风险。

---

## 5. 风险分析

| 风险 | 等级 | 缓解 |
|---|---|---|
| R1 两条 RelationshipState 并存易混淆 | 中 | 4.2-D 只接新体系；文档明确旧 5 维为 legacy |
| R2 LLM EventExtractor（legacy）与规则 extractor（relationship）双轨产出事件 | 中 | 转换器只消费新体系 RelationshipEvent，不接 legacy 提取器 |
| R3 preference 域提案可能间接影响人格 | 低 | 必须走既有 approval 审批流；evaluator preference 阈值（confidence≥0.6/stability≥0.4/consistency≥0.4/impact≥0.1）天然要求多次验证 |
| R4 类型标注被滥用（把关系事件标成 preference 绕过红线） | 低 | 转换器映射规则写死 + 测试断言：relationship 事件类型永不进 preference/trait（复用 case3 断言形态） |
| R5 digest 既有失败测试（case3 等 4/5） | 已存在 | 修复后 case3 转绿即本阶段回归证据；其余失败属既有基线债，如实汇报 |

---

## 6. 验收目标设计（实施阶段用）

一次长期互动后：

```
RelationshipEvent（含 preference_learning）
    ↓
digest raw event（类型如实，evidence 锚定 experience_id）
    ↓
Normalizer → Validator → Matcher → GrowthEvaluator
    ↓
GrowthProposal
    ├─ evidence_ids 含对应 experience_id ✓
    ├─ preference 类：domain=preference，受阈值+approval 门控 ✓
    └─ relationship 类：domain=relationship_context，applied_delta=0 ✓
```

红线断言（沿用 4.2-B/C 形态）：

- `event_type=="relationship"` 的事件永不产生 preference/trait 层成长
- relationship_context 域提案 applied_delta 恒为 0
- 无 IDENTITY_CORE / PersonalityResolver 改动（git diff 可证）
- 无新增 Growth / Relationship / 提案系统（仅一处薄转换器）

---

## 7. 给顾问的请示点

1. 是否批准 §4 的薄转换器方案作为 4.2-D 实施范围？
2. Stage 4（GrowthAdapterSpec.evaluate 实现）是否明确**排除**在 4.2-D 之外、留待后续阶段？
3. legacy GrowthPipeline 的 trust+0.05 朴素路径维持冻结、不在本阶段处理，是否确认？
