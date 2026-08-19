# Phase 4.0.4-P4 Runtime Stage 0-16 有效性审计报告

> 日期：2026-08-11
> 章程依据：第四阶段「Runtime 职责修正」——审计 Stage 0-16，三分类：已有效 / 部分有效 / 暂未启用
> 性质：纯审计，未修改任何代码（「不为填充 Stage 而增加代码」）

---

## 0. 前置澄清：双 RuntimeCore 不是平行实现

- `src/runtime/runtime_core.py`（4751 行）= **唯一真实实现**，组合持有 `LifecycleExecutor`，实现 `_stage_00` ~ `_stage_16` 全部方法。
- `src/runtime/runtime.py`（4319 行）= **兼容壳层**：`process()/start()/shutdown()` 开头均优先委托 `self._impl`（即 runtime_core 的 RuntimeCore），仅委托失败才走自建旧逻辑。
- 生产注入链：`api_server._init_phase72_pipeline()` → `_runtime_bridge.runtime_core` 或 `src.runtime.runtime.impl` → 最终都落在 runtime_core 实现上。
- 修正此前初审报告「两个平行 RuntimeCore」的表述：是「壳 + 实现」关系，但壳内仍保留 4000+ 行自建旧逻辑，属可归档资产（章程：不删，仅标记）。

调度契约（`lifecycle_executor.py`）：17 阶段严格按 `RUNTIME_LIFECYCLE_ORDER` 遍历；单阶段异常 fail-soft 记入 `ctx._phase_errors` 不中断；Stage 1 后 `_on_event` exactly-once（防双重状态污染，有测试锁）。

## 1. 逐阶段分类

| Stage | 名称 | 分类 | 依据 |
|-------|------|------|------|
| 0 | CONTROL_CHECK | ⚪ 暂未启用 | 读取 duck-typed `_control_state`，但 runtime_core 不持有该属性、生产无注入方 → 永远全放行 |
| 1 | RECEIVE_EVENT | ✅ 已有效 | 提取 user_message 入 ctx + exactly-once 事件契约 |
| 2 | MEMORY_RETRIEVAL | 🟡 部分有效 | `memory_adapter` 真实构造（runtime_core.py:372）；但 adapter.retrieve(query) 无 user_id 通道（P3 遗留观察项）；降级路径 `get_recent(limit=20)` 无用户过滤 |
| 3 | EMOTION_UPDATE | ✅ 已有效 | `EmotionManager` 真实构造（:665），update_from_event + 快照入 ctx |
| 4 | GROWTH_EVALUATION | 🟡 部分有效 | `GrowthAdapter` 真实构造（:386）；SAFE 模式下自动更新被禁（设计如此），实际产出取决于 GrowthProposal 审核链 |
| 5 | PERSONALITY_UPDATE | ✅ 已有效 | `PersonalityResolver`（:409）优先，adapter snapshot 降级 |
| 6 | PERSONALITY_CONTEXT_BUILD | ✅ 已有效 | 人格快照→自然语言；末尾经 IdentityContextBuilder 产出 `identity_context_text`（Identity ⊥ Personality 边界清晰）。注：`identity_anchor_enabled=false`，锚点子功能关闭 |
| 7 | PERCEPTION_OBSERVATION | ⚪ 暂未启用 | 显式 no-op 占位（注释标注 Phase 4.1.0 激活） |
| 8 | PERCEPTION_ANALYSIS | ⚪ 暂未启用 | 显式 no-op 占位（Phase 4.2.0 Vision） |
| 9 | SELF_MODEL_BUILD | ⚪ 暂未启用 | 显式 no-op 占位——尽管 `src/runtime/self_model/` 已有 45 个文件，**未接入此阶段**（资产在库外循环） |
| 10 | SELF_MODEL_EVOLUTION | ⚪ 暂未启用 | 同上（Phase 4.5 激活标注） |
| 11 | SELF_MODEL_REFLECTION | ⚪ 暂未启用 | 同上（Phase 4.7 激活标注） |
| 12 | SELF_MODEL_VALIDATION | ⚪ 暂未启用 | 同上 |
| 13 | SELF_MODEL_PERSISTENCE | ⚪ 暂未启用 | 同上（Phase 4.6 激活标注） |
| 14 | RESPONSE_GENERATION | 🟡 部分有效 | 双降级设计（ResponseAdapter → engine ref → legacy）；生产是否由 Runtime 直接产出回复取决于 adapter_registry 是否注入 `response_adapter_impl`，未注入时每轮都空转后落回 orchestrator legacy（E1 链路） |
| 15 | GUARD_CHAIN | ⚪ 暂未启用 | 仅当 bridge 注入 `response_guard_chain` 才生效；当前无证据显示生产注入 |
| 16 | RESPONSE | ✅ 已有效 | `_final_reply` → `finalized_reply` 同步（trivial） |

**统计：已有效 5 / 部分有效 3 / 暂未启用 9**

## 2. 关键结论

1. **Runtime 骨架已是真生命周期层**（Stage 1-6 + 16 闭环可用），不再是空壳——章程阶段四的底层目标部分达成。
2. **最大断裂点**：Stage 9-13 的 SelfModel 系列全空，而 `src/runtime/self_model/` 45 个文件的已实现能力在阶段链之外自转。这正是章程说的「拥有大量模块但连接不完整」的典型断面——但按章程「优先接通已有模块」，下一步应是**接线而非新写**。
3. **Stage 14 是 Phase4 Prompt 路径（E4）启用与否的咽喉**：只有 response adapter 真实注入且产出稳定，E4 灰度才有意义。当前生产回复实际仍由 orchestrator legacy（E1）产出，Runtime 路径多数情况下是"空转 + fallback"。
4. **Stage 0/15 两个安全闸门未接线**：control_state 与 guard_chain 都有机制无注入，安全防线目前靠 P3 之前的 API 层修复兜底。

## 3. 建议（不在本阶段执行）

- 接线优先级：Stage 9（SELF_MODEL_BUILD，资产最厚）→ Stage 14（response adapter 注入验证）→ Stage 15（guard chain）。
- 每接一个 Stage 按章程走完整流程（审计→最小方案→测试→报告）。
- `runtime.py` 壳内旧逻辑建议标记 deprecated 注释，不删除。

## 4. 架构影响

无代码变更。本报告闭合章程阶段四的审计要求，并为「是否启用 Phase4 Prompt 路径」给出前置判断：**暂不具备**，需先完成 Stage 14 注入验证与灰度。
