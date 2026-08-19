# Phase 4.2 第一步 · Relationship 能力地图（只读审计）

> 日期：2026-08-12
> 范围：只读审计，未修改任何文件
> 审计问题：① src/relationship 资产 ② Runtime 接入 ③ Orchestrator 来源
> ④ prompt 通路 ⑤ 假实现识别

---

## 0. 核心发现（一句话）

**HEAD 提交 `eed2de6`（Phase 7.2.1）把 `relationship_event.py` 从旧 dataclass
替换成了 R2.5.2-B TypedDict（两套 schema 完全不同），但没有迁移任何旧 API
消费方——自那时起，Relationship 的全部写入路径在生产中静默失效：**
extractor 产出变成裸 dict → evaluator 读属性 AttributeError →
IntelligenceEngine / Orchestrator 后处理 / 16 个测试全部卡死。
而新契约世界（memory gates → relationship_memory → context → boundary）是健康的。

## 1. 资产清单（src/relationship/ 14 模块 + 周边）

### ✅ 已完成且健康（新契约 R2.5.2-B 世界，测试全过）

| 模块 | 职责 | 验证 |
|------|------|------|
| relationship_event.py | 关系事件 **TypedDict**（观察级，源自 memory gates） | gates 测试通过 |
| relationship_memory.py | 关系事件库 / store | test_phase40_r252b gates ✅ |
| relationship_context.py | 关系上下文聚合视图 | test_relationship_context ✅ |
| relationship_boundary.py | 关系边界 | test_relationship_boundary ✅ |
| relationship_repository.py | 持久化 `data/{user}/relationship_{state,model,...}.json` | test_relationship_repository ✅ |

### ⚠️ 部分完成 / 断裂（旧契约世界，eed2de6 起失效）

| 模块 | 状态 | 断裂点 |
|------|------|--------|
| relationship_event_extractor.py | **从用户消息提取关系事件**（关键词模式，无需 LLM） | 按旧 dataclass API 构造 `RelationshipEvent(event_id=...)`，实际得到 TypedDict 裸 dict（5 测试失败） |
| relationship_evaluator.py | 事件评估门（类型/信号/证据/维度校验） | 读 `event.event_type` 属性 → AttributeError（9 测试失败） |
| relationship_model.py | **RelationshipIntelligenceEngine**（Phase 3.5.27）：extract→evaluate→分维度 delta（trust/familiarity/collaboration）→SharedExperience→里程碑→relationship_stage 迁移 | 链路被上游契约断裂卡死（1 测试失败）。**这是最符合您设计理念的资产**——不是好感度计数器，而是「羽依如何理解与这个人的长期关系」 |
| relationship_state.py / relationship_change.py | 状态与变更记录 | 被 engine 使用，随引擎一起失效 |
| relationship_profile.py / cognitive_profile.py | Phase 10 长期档案 | test_relationship_data 1 失败（旧 API 残留） |

### ❌ 未接入（零调用方）

| 模块 | 说明 |
|------|------|
| relationship_context_provider.py | 关系上下文提供器，全库无调用 |
| relationship_runtime_adapter.py | **Phase C.5 只读适配器（812 行）**，实现 CycleAdapter 协议，全库无实例化 |
| relationship_influence_profile.py | Phase 7 画像（repository 兼容层仍支持） |

## 2. Runtime 接入现状

**已具备（比预期完整）：**
- `relationship_enabled` 配置门（默认 False）→ 装载 repository/state/model/engine 四件套（runtime_core:671-685）
- `record_relationship_interaction()` **完整实现**（2864-2902）：process_interaction → 双 save → notify_relationship_changed 事件
- 快照接口 `get_relationship_state_snapshot` / `get_relationship_model_snapshot`、health 注册、reflection_evaluator 的 relationship 评估入口

**缺口：**
- 17 个 lifecycle stage 中**无 relationship 阶段**
- **Stage 14 fallback `relationship_context={}` 硬编码**（4986 行）——与 Phase 4.1 前 self_model 完全同款的缺口
- `record_relationship_interaction` **生产零调用方**——写路径建成但从未被触发

**既有失败测试 `test_relationship_intelligence_runtime` 的双重病因：**
1. 测试 config 未开 `relationship_enabled: True` → 组件全 None → 返回 None
2. 即使开门，evaluator 契约断裂 → AttributeError → 被 try/except 静默成 None

## 3. Orchestrator 现状（平行世界）

- **第三套关系状态**：`RelationshipState` 薄包装 → `src/personality/relationship_state`（v0.6，持久化 `data/relationship_state.json`）——与 src/relationship 版、repository 版并存
- `_process_relationship_post`（orchestrator.py:1510）：trust±0.05 / familiarity+0.02 的**朴素好感度加减法**——正是您明确否定的方向；且同样被契约断裂卡死（构造旧 API event → evaluator AttributeError → 静默跳过），**生产上自 eed2de6 起空转**
- prompt 通路：assembled_context.relationship_profile → relationship_ctx → `engine.generate(relationship_context=...)` ✅ 存在

## 4. Prompt 通路 ✅

- `prompt_builder.build_messages(relationship_context=...)` 支持并有测试锁定
- `engine.py` 渲染关系区块（211-213、329-330 行）

## 5. 既有测试健康度

- 新契约世界：63 通过（memory gates/context/boundary/repository/runtime_integration）
- 旧契约世界：**16 失败**（evaluator 9 / extractor 5 / data 1 / engine 1），
  全部归因于同一个契约断裂，非 16 个独立问题

## 6. 第二步建议（接线前的必要顺序）

```
① 统一事件契约（修断裂，非新建）
   extractor/evaluator 迁移到 R2.5.2-B TypedDict schema
   （或恢复兼容层）→ 16 个旧测试转绿，IntelligenceEngine 复活
        ↓
② 接通写路径：Runtime 每轮 Interaction → record_relationship_interaction
   （生产零调用 → 接入 event 流；开启 relationship_enabled）
        ↓
③ 接通读路径：Stage 14 relationship_context 接快照
   （对齐 Phase 4.1 self_model 的做法，消灭 {} 硬编码）
        ↓
④ Orchestrator 朴素加减法后处理退役为 fallback
   （唯一权威 = IntelligenceEngine，消除第三套状态）
```

严格遵循您的架构判断：Relationship **先影响回复方式**，长期观察经 Growth
评估才可能影响人格——本阶段不接 Personality 变更链路。
