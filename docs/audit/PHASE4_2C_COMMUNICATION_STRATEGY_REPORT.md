# Phase 4.2-C 实施报告：Relationship Context → Communication Strategy

日期：2026-08-12
前置：4.2-C 审计（`PHASE4_2C_COMMUNICATION_STRATEGY_AUDIT.md`）+ 批准任务卡（方案 C）
性质：接线优先。零新增 Relationship 系统、零 LLM 策略、零评分系统。

---

## 1. 修改文件（共 2 个）

### `src/runtime/runtime_core.py`（唯一源码改动，约 +170 行）

| 位置 | 改动 |
|---|---|
| 新增类常量 `_COMM_STRAT_DEPTH_MAP` / `_COMM_STRAT_REF_MAP` | relationship_stage → 策略的静态映射（deep_collaboration/stable→advanced+direct；developing→standard+optional；initial→basic+minimal） |
| 新增 `_build_communication_strategy(ctx)` | 从 `ctx.relationship_snapshot` + `relationship_model_runtime` 派生策略 dict：`explanation_depth` / `context_reference` / `avoid_redundancy` / `shared_experience_usage` / `relationship_stage`，可选 `shared_experience_topics`（最近 3 条共同经历切片）与 `dominant_emotion_tone`（top-2 情绪基调）。只读、fail-soft 返回 {} |
| 新增 `_build_communication_strategy_block(strategy)` | 策略 dict → 「【互动策略参考】」system 块。三重红线：①措辞只描述交流策略；②禁用措辞硬过滤（更亲密/更依赖/主人/好感度/亲密度 → 整块丢弃）；③RelationshipBoundary 检查（非 SAFE 或 boundary 自身异常 → 整块丢弃） |
| `_stage_14_response_generation` | 开头派生策略并挂 `ctx.communication_strategy`（观测/测试用）；ResponseAdapter req_kwargs +`communication_strategy` / `communication_strategy_block` 两键；engine 降级路径 context_prompt_blocks 扩展为 identity 块 + 策略块（`prompt_blocks or None`） |

### `tests/runtime/test_relationship_communication_strategy.py`（新增，5 条验收测试）

## 2. 调用链变化

**接线前**（4.2-B 后）：
```
Relationship → relationship_context（阶段+概述两行信息）→ Prompt
= 告诉模型"关系是什么"
```

**接线后**：
```
Stage 3 子步骤：relationship_snapshot（C.5 只读快照）
  ↓
Stage 14 开头：_build_communication_strategy（静态规则派生）
  ├─ 数据源：stage / shared_experiences / emotional_patterns / interaction_history
  └─ 策略：explanation_depth / context_reference / avoid_redundancy / shared_experience_usage
  ↓
_build_communication_strategy_block（措辞红线 + Boundary 检查）
  ↓
注入两条现成通道：
  ├─ ResponseAdapter req_kwargs["communication_strategy"/"communication_strategy_block"]
  └─ engine context_prompt_blocks += 【互动策略参考】system 块
= 根据关系决定"应该怎么交流"
```

策略语义示例（测试实证）：新用户 → `basic / minimal / 不去重 / 无共同经历`；
长期协作用户（stable）→ `advanced / direct / 去重 / optional + 共同经历主题`。
**不是"更喜欢"，是"关系历史 → 上下文理解增强"**（任务卡定义）。

## 3. 测试结果（`"$DAIMON_USER_PYTHON" -m pytest`）

| 套件 | 结果 |
|---|---|
| **新增 5 条验收测试** | **5/5 全绿** ✅ |
| `tests/runtime` 全量 | 170 passed / 1 failed（唯一失败为既有 fallback 语义问题，按决议留待后续统一处理） |
| `tests/test_runtime_stage_contract.py` | **10/10 全绿** ✅（17 阶段冻结契约零改动） |
| relationship 领域全量（`-k "relationship and not digest"`） | **244 passed / 0 failed** ✅ |

验收对照任务卡：

| 任务卡要求 | 结果 |
|---|---|
| 1. 策略差异 | ✅ 新用户 `basic/minimal` vs 长期协作 `advanced/direct/avoid_redundancy/optional`（真实互动驱动到 stable 后断言，非 mock 状态） |
| 2. 身份不变 | ✅ IDENTITY_CORE 深拷贝逐字节一致 |
| 3. 人格不污染 | ✅ SelfModel 提案队列为空、人格特质读取不被破坏 |
| 4. 红线 | ✅ 策略块无 更亲密/更依赖/主人/好感度/亲密度；Boundary BLOCK 时整块丢弃且回复正常 |
| 5. fail-soft | ✅ relationship 关闭 → 策略为空、无策略块、回复正常 |

## 4. 架构影响

- **Relationship 从「存档系统」变成「认知系统」**：三层完备——
  知道发生过什么（Memory/Experience）→ 知道一起经历过什么（4.2-B）→
  知道因为这些经历应该怎样理解你的表达（4.2-C）。
- **策略层定位正确**：策略派生是 Response 策略层的**只读投影**——
  Relationship 事实 → 系统规则 → 表达策略，不经过 Personality/Identity，
  避免了「关系变化 → communication_style 变化 → 人格漂移」的退化路径
  （任务卡明确禁止的 communication_style 复活未发生）。
- **双通道纪律保持**：RelationshipContextProvider / communication_style /
  confirmed_patterns 三个休眠资产按审计结论未复活，C.5 快照仍是唯一关系上下文来源。
- **可解释性**：策略完全由静态映射与已有数据派生，每条策略块措辞可审计、
  可被 Boundary 拦截，无 LLM 参与策略生成。

**三项原则保持确认**：Identity 唯一来源 ✅（红线测试锁死）；
Runtime 唯一生命循环 ✅（全部接线在 Stage 14 内，无平行调度）；
Relationship ≠ 人格修改 ✅（红线测试锁死）。

## 5. 遗留事项

| # | 事项 | 说明 |
|---|---|---|
| a | Response fallback 语义统一 | 按顾问决议继续冻结（优先级排在生命循环主线之后） |
| b | 策略消费端在 LLM 侧的实际效果 | 策略块已进入 prompt，但「解释深度」等指令对真实模型输出的影响需真实对话观察（路线图第 3 项：全链路真实对话测试） |
| c | Growth 消费 Relationship evidence | 路线图第 2 项（4.2-D 方向）：Growth 评估时引用关系事件作为证据，仍不直接改人格 |
| d | `shared_experience_topics` 素材范围 | 当前仅 collaboration 类事件产生共同经历；preference_learning 等类型的经历沉淀可作为后续增强 |
| e | orchestrator 朴素 trust±0.05 路径 | 仍在，退役留待后续阶段 |
| f | engine_context 组合污染 / growth_digest 既有失败 | 维持既有登记，与本阶段无关 |

## 6. 结论

4.2-C 验收达成：Relationship Context → Communication Strategy → Response Generation
链路贯通，5 条验收测试全绿，17 阶段冻结契约零改动，三项架构原则保持。
羽依现在能基于共同经历调整交流策略（解释深度/背景补充/经历引用/减少重复），
而身份与人格核心完全不受影响。
