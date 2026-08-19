# Phase 4.2-D — Relationship → Growth 证据链集成报告

日期：2026-08-12
性质：薄转换器实施（经顾问批准，2026-08-12 任务卡）

---

## 0. 范围与红线遵守

| 任务卡禁令 | 遵守证据 |
|---|---|
| 不修改 GrowthEvaluator | `git diff` 无 `src/growth/growth_evaluator.py` |
| 不修改 PersonalityResolver | 无改动 |
| 不修改 IDENTITY_CORE | 无改动 |
| 不新增 Relationship Growth 系统 | 仅 1 个薄转换器 + 1 处 fail-soft 转发 |
| Relationship 不直接产生人格变化 | 所有变化必须经 Evaluator → Proposal(pending) → Approval |

**改动清单（共 3 个文件）：**

| 文件 | 性质 | 说明 |
|---|---|---|
| `src/growth/relationship_evidence_adapter.py` | 新增 | 薄转换器（证据翻译层，Growth 侧） |
| `src/runtime/runtime_core.py` | +44 行 | `record_relationship_interaction` 内 fail-soft 转发 |
| `tests/test_phase_4_2d_relationship_growth_evidence.py` | 新增 | 22 条验收测试 |

---

## 1. 实现的链路

```
RelationshipEvent（4.2-B 写路径产出，统一契约）
        ↓ RuntimeCore._forward_relationship_event_to_growth（fail-soft）
RelationshipEvidenceAdapter.convert（类型如实翻译，唯一新逻辑）
        ├─ preference_learning → event_type="preference"
        └─ 其余 10 种类型      → event_type="relationship"
        ↓
EventNormalizer → 恢复结构化权威 event_type → EventValidator
        ↓
EventHistoryMatcher → GrowthEvaluator（红线在此生效，未改一行）
        ↓ growth_allowed 才继续
GrowthIntegrationService.process_event（既有 Approval 流入口，未改）
        ↓
GrowthProposal（status=pending，auto_accept=False 冻结）
        ↓
Approval Flow（既有，未改）
```

### 关键设计决策（如实披露）

**Normalizer 之后恢复 event_type。**
EventNormalizer 的关键词重分类面向不可信自由文本，其 `VALID_EVENT_TYPES`
白名单不含 `"preference"`，直接喂入会把 preference 证据重分类为
`growth`/`memory`（这正是 digest 基线 case1 既有失败的根源之一）。
RelationshipEvent 携带的是**结构化权威类型**，因此适配器在 normalize
（拿 canonical_topic / event_id / 标准化证据）之后恢复 `event_type`，
保证 Evaluator 的领域映射如实。该决策只影响本适配器的新数据流，
不改变 Normalizer 对既有自由文本路径的行为。

---

## 2. 转换规则（冻结）

| RelationshipEvent.type | digest event_type | Evaluator 域 | 上限 |
|---|---|---|---|
| `preference_learning` | `preference` | preference | preference 层 |
| 其余全部（collaboration/trust_building/boundary_respect/promise/declaration/milestone/support/boundary/shared_activity/other） | `relationship` | relationship_context | **context 层，applied_delta 恒 0** |

转换器**绝不**生成 trait / personality_change / 变化量——
那是 Evaluator + Approval 的职责。

---

## 3. 验收结果（22/22 通过）

| 验收项 | 测试 | 结果 |
|---|---|---|
| #1 Preference 证据：长期互动「用户多次要求详细技术解释」→ GrowthProposal pending | `TestPreferenceEvidenceProposal` | ✅ created / pending / domain=preference |
| #2 红线：关系事件不产生 PersonalityTraitChange / CoreValueChange / IdentityChange | `TestRelationshipRedLines`（5 类型参数化 + 映射写死断言） | ✅ domain=relationship_context / level≤context / applied_delta=0 / proposal 无 personality.* 路径 |
| #3 Evidence 可追踪：evidence_ids + source=relationship | `TestEvidenceTraceability` | ✅ proposal.evidence_ids 含原证据 id；evaluator_meta.source="relationship"、relationship_event_id/type 齐全；source_event_id 溯源 |
| #4 原有 Growth 测试不回归 | 见 §4 | ✅ |

附加保障：

- `TestConvertMapping`：11 种类型映射全覆盖 + 非法输入返回 None
- `TestRuntimeWiring`：生产接线端到端（RuntimeCore → 领域事件 payload 含 `growth_evidence_state`）+ fail-soft（None/非法输入/内部异常均隔离）

---

## 4. 回归结果

| 套件 | 基线（改前） | 改后 | 结论 |
|---|---|---|---|
| `tests/runtime` | 170P / 1F（test_identity_context_phase2 fallback，**既有冻结项**） | 170P / 1F（同一项） | 无回归 |
| growth/relationship 核心（13 文件 + tests/growth） | 63P（子集） | **254P / 0F**（扩大范围全量） | 无回归 |
| `test_growth_digest_baseline_r2_5_0_b` | 4F / 1P（既有基线债） | 4F / 1P（不变） | 未触碰 |

4.2-D 验收测试：**22 / 22 通过**。

---

## 5. 顾问任务卡三项决策的落实

1. **薄转换器放在 Growth 侧** ✅ —— `src/growth/relationship_evidence_adapter.py`，
   Relationship 模块零改动（"发生了什么关系事件"归 Relationship，
   "证据是否支持长期变化"归 Growth）。
2. **Stage 4（GrowthAdapter.evaluate 空转）排除本阶段** ✅ —— 未触碰，
   留待建议的 Phase 4.3 Runtime Growth Activation。
3. **legacy trust+0.05 数值路径维持冻结** ✅ —— `src/growth/pipeline.py`
   及其 5 维 RelationshipState 零改动。

---

## 6. 现在的一次完整对话链路

```
用户输入
 ↓
Memory（4.1D Experience→Memory 已通）
 ↓
Emotion / Relationship（4.2-B 写路径）
 ↓                                    ↘
Communication Strategy（4.2-C）   RelationshipEvent
 ↓                                    ↓ 【本次接通】
Response                       Growth digest 管线
 ↓                                    ↓
Experience Journal             GrowthProposal（pending，证据锚定）
 ↓                                    ↓
SelfModel                        Approval Flow
```

关系经历 now 是**成长判断的证据来源**，而不是直接的人格修改器——
符合「经历不会直接改变她，而是成为未来变化的证据」。

---

## 7. 遗留与建议（如实汇报）

| 项 | 状态 | 建议 |
|---|---|---|
| Stage 4 GrowthAdapter.evaluate 空转 | 冻结（本阶段排除） | Phase 4.3 Runtime Growth Activation 单独立项 |
| digest 基线 4F/1P | 既有债，未触碰 | 修复后 case1（preference 重分类）可作为本适配器"恢复 event_type"决策的对照证据 |
| legacy trust+0.05 / 5 维 RelationshipState | 冻结 | 退役议题单列，不与本阶段混合 |
| relationship_context 域提案置信度（TYPE_WEIGHTS 0.4 → 难过 0.8 门槛） | 设计使然 | 关系证据多数停在 no_growth / rejected_low_confidence，是红线的一部分，非缺陷 |
| Runtime 接线目前仅覆盖 Extractor 产出的事件；Bridge 产出的 RelationshipEvent（build_relationship_event_from_record）未接入 | 范围外 | 如需覆盖，同一适配器可直接复用（convert 只吃统一契约 dict） |
