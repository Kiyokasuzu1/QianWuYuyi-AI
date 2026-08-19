# Phase 4.2-C 审计报告：Relationship Context → Communication Strategy

日期：2026-08-12
性质：**纯审计，未改任何代码**（按任务卡要求）
前置：4.2-B 已接通 record → State → relationship_context → Response（`PHASE4_2B_RELATIONSHIP_RUNTIME_INTEGRATION_REPORT.md`）

---

## 0. 任务卡核心辨析

任务卡明确：4.2-C **不是**「让关系状态影响交流方式」（易误解为增加亲密语气），而是

> Relationship Context → **Communication Strategy** 接线：
> 是否补充背景 / 是否引用共同经历 / 是否调整解释深度 / 是否减少重复说明。

即：关系不改变羽依是谁，只改变互动策略。审计按此标准评估接入点。

## 1. 策略入口搜索结论（communication_style / interaction_profile / ResponseStrategy）

### 1.1 活路径（17 阶段 RuntimeCore.process，生产聊天路径）

| 资产 | 位置 | 现状 |
|---|---|---|
| `ctx.personality_context_text` | Stage 6 产出（top-5 特质文本） | 活，进 Stage 14 |
| `ctx.identity_context_text` | Stage 6 末尾 IdentityContextBuilder | 活，经 context_prompt_blocks 进 engine |
| `ctx.relationship_snapshot` → `_build_relationship_prompt_context` | **4.2-B 新增**（Stage 3 子步骤 → Stage 14） | 活，但只注入「阶段+概述」两行**信息**，不含策略 |
| `resolved_behavior` / `chosen_expression` / `expression_constraint_text` | ResponseRequest 9 字段链 | **17 阶段路径中不存在**（engine 降级路径 `resolved_behavior={}` 硬编码） |

### 1.2 休眠路径（生产零调用，不要误接）

- `ResponseAdapter.build_request()`（response_adapter.py:221）——完整 ResponseRequest 管线
  （含 communication_style 注入、`relationship_context_text` 字段映射 :536/:567），
  **仅 legacy `src/runtime/runtime.py:3779` 调用**，17 阶段路径不使用。
- `RelationshipContextProvider.get_context(state, profile)`
  （relationship_context_provider.py）——现成的【关系认知参考】自然语言块
  （熟悉/信任/协作/沟通风格/互动模式/阶段 + RelationshipBoundary 检查），
  **生产零调用**（仅测试引用）。
- `RelationshipState.communication_style: List[str]` —— **死字段**：除 from_dict 恢复外
  无任何生产写入方。
- `RelationshipCognitiveProfile.confirmed_patterns` —— repository 有 load/save，
  **无生产写入方**（死累积）。`personality/self_narrative_context.py` 消费它，
  但 self_narrative 亦不在 17 阶段活路径上。
- `interaction_profile` / `ResponseStrategy`：**全仓库不存在**，无需兼容。

### 1.3 4.2-B 起新累积的活数据（策略的真实数据源）

- `RelationshipModel.shared_experiences`（collaboration 事件落库，含 topic/description[:120]/confidence）
- `RelationshipModel.emotional_patterns`（emotion_tag 计数）
- `RelationshipModel.interaction_history`（每轮记录，含 event_type/stage_after）
- C.5 快照的 `labels`（熟悉/信任/协作/频率的中文标签）与 `stage_label`

## 2. Prompt 消费链确认

```
RuntimeContext（Stage 3 子步骤写 relationship_snapshot）
  ↓
Stage 14 _stage_14_response_generation
  ├─ ResponseAdapter 路径：adapter.generate(req_kwargs)
  │    req_kwargs["relationship_context"] = _build_relationship_prompt_context(ctx)  ← 4.2-B
  └─ engine 降级路径：engine.generate(...,
         relationship_context=_build_relationship_prompt_context(ctx),               ← 4.2-B
         context_prompt_blocks=[identity_ctx system block])                          ← 现成通道
  ↓
src/engine.py 渲染：
  relationship_context dict → 【用户关系】k: v 行（engine.py:211-217 / 329-332）
  context_prompt_blocks → 原样 system 块（engine.py:337+）
```

**两条现成注入通道**：① relationship_context dict（信息型）；② context_prompt_blocks
（自由 system block，适合策略指令型）。无需新增任何引擎参数。

## 3. 接入点评估

| 方案 | 说明 | 评估 |
|---|---|---|
| A. 复活 RelationshipContextProvider 替换 4.2-B 派生 | 休眠资产，数值→文字+边界检查 | ❌ 与 C.5 summary 功能重叠会形成双通道；且不含 shared_experiences（策略核心数据源），输出仍是「信息」不是「策略」 |
| B. 复活 communication_style / confirmed_patterns 死字段 | 需新增写入方设计 | ❌ 超「接线」范围，等于新建累积系统，留待后续（若需要） |
| **C. 扩展 4.2-B 派生层 + context_prompt_blocks 策略块** | 在 `_build_relationship_prompt_context` 同级新增策略派生：从 ctx.relationship_snapshot + relationship_model_runtime 的活数据，按**系统规则**（非 LLM）生成互动策略 | ✅ 推荐。接入点在 Response 策略层（Stage 14 派生），不动 Personality/Identity；复用两条现成通道；全部改动集中在 runtime_core |

### 推荐形态（方案 C 细化）

数据源 → 策略映射（系统规则，可审计）：

| 数据 | 策略派生 |
|---|---|
| `stage_label` / `labels` | 解释深度与背景补充：深协作/高熟悉 → 「可减少背景重复说明，可直接进入问题核心」；初识 → 「适当补充背景」 |
| `model.shared_experiences`（最近 N 条 topic/description） | 共同经历引用：「你们曾一起：…」（用户自己的话切片，非编造） |
| `model.emotional_patterns`（top tags） | 互动节奏参考（如 joy 高频 → 轻松基调可行） |
| `interaction_history` 长度 | 去重说明：长期互动 → 避免重复自我介绍式说明 |

注入形态：
1. `relationship_context` dict 增补键（共同经历主题、互动模式）→ 【用户关系】块；
2. `context_prompt_blocks` 增补一条「互动策略」system block（策略指令文本，
   过 RelationshipBoundary 检查，违规整块丢弃 fail-soft）。

红线保持：策略文本由**系统静态规则**生成；不修改 Personality/Identity/SelfModel；
不引入亲密度数值进 prompt（标签化沿用 C.5 现有中文标签）。

## 4. 验收设计（按任务卡：同输入、不同 relationship_context → 策略不同，Identity/Personality 不变）

新增 `tests/runtime/test_relationship_communication_strategy.py`：

1. **策略分化**：两个 RuntimeCore 实例（A=全新无互动；B=多轮 collaboration 互动后），
   同一捕获 engine、同一用户输入 → 断言 B 的 relationship_context / context_prompt_blocks
   与 A 不同（B 含共同经历与策略块，A 无）。
2. **策略内容正确性**：断言策略块包含 stage 对应的映射文本与 shared_experience 主题。
3. **边界红线**：构造触发 RelationshipBoundary 的内容 → 策略块被整块丢弃，回复仍正常。
4. **身份人格不变**：IDENTITY_CORE 逐字节不变、_pending_self_model_suggestions 为空
   （复用 4.2-B Test 5 断言集）。
5. **fail-soft**：relationship_enabled=False → 无策略块、无 relationship_context，回复正常。

## 5. 风险与注意

- **共同经历引用的素材边界**：shared_experiences.description 是用户消息前 120 字切片
  ——把用户自己的话还给模型是安全的，但仍需过 RelationshipBoundary（方案已含）。
- **不要做成语气修改器**：策略块措辞必须是「互动策略」（背景/深度/引用/去重），
  禁止出现「更亲密/更喜欢」类指令（任务卡红线）。
- **双引擎并存**：src/engine.py（活路径）与 src/response/engine.py（ResponseAdapterImpl
  懒加载路径）都消费 relationship_context；4.2-C 只接活路径，legacy 不动。
- **C.1/legacy ResponseAdapter 管线休眠**：不要在 4.2-C 顺手激活 build_request 管线
  （超范围）。

## 6. 结论

接入点明确：**Stage 14 派生层扩展（方案 C）**——数据源（shared_experiences /
emotional_patterns / labels，4.2-B 起真实累积）已活，注入通道（relationship_context dict +
context_prompt_blocks）已活，缺的只是一层**系统规则的策略派生**。
改动可集中在 runtime_core 一处 + 一个新测试文件，符合最小修改原则。
等批准后按「最小修改 → 测试 → 影响报告」实施。
