# 羽依核心 → 当前仓库 迁移差异报告

> 生成时间：2026-07-30
> 源目录：`D:\Yuyi Project\QianWuYuyi-AI_自动驾驶版\QianWuYuyi-AI\羽依核心\yuyi_core_backup`
> 目标目录：`D:\Yuyi Project\QianWuYuyi-AI_自动驾驶版\QianWuYuyi-AI`

---

## 0. 总体观察

| 维度 | 羽依核心/yuyi_core_backup | 当前仓库 |
|---|---|---|
| `core/` | 9 个 .py + 1 个 .backup | 9 个 .py（同名同数） |
| `context/` | 3 个 .py | 3 个 .py（同名同数） |
| `contracts/` | 5 个 .py | 34 个 .py（**显著扩展**） |
| `growth/` | 20 + 5 (proposal/) | 25 + 5 (proposal/) + 4 (sync/) |
| `memory/` | 15 个 .py + 1 .backup | 16 个 .py（多 `memory_consolidation_engine.py`、`memory_relevance_evaluator.py`） |
| `personality/` | 37 个 .py | 56 个 .py（**显著扩展**，含 self_model 系列 12+ 新模块） |
| `emotion/` | — | 24 个 .py（**新模块**） |
| `data/` | 4 个数据 + 1 个 chroma_db 备份 | 多个运行数据（**禁止覆盖**） |
| `config.yaml` / `.env` | 存在 | `config.yaml` 存在，**无 .env**（不复制） |
| `admin/` / `runtime/` / `safety/` / ... | — | **新增模块**（第三类） |
| `static/admin/` | — | 完整前端管理面板（第四类） |

**关键结论**：
1. 当前仓库是**已大幅演进**的开发版本，包含了 `yuyi_core_backup` 之后的所有新增能力（emotion、admin、runtime、self_model 系列扩展、sync 模块等）。
2. 羽依核心（yuyi_core_backup）整体是**早期稳定快照**，与当前仓库存在交集但版本更早。
3. **不存在"羽依核心 → 当前仓库"的简单复制关系**；多数文件当前仓库已为更新版。
4. **真正需要从羽依核心拉取到当前仓库的文件极少**，主要是要确认"哪些早期补丁/修复尚未进入当前仓库"。

---

## 1. 第一类：绝对保护（禁止覆盖）

| 文件/目录 | 标记 | 原因 |
|---|---|---|
| `data/`（全部子文件） | **[禁止修改]** | 长期记忆、成长状态、Embeddings、用户历史 |
| `data/memory.json` | **[禁止修改]** | 羽依核心数据 |
| `data/chroma_db/` | **[禁止修改]** | 向量记忆数据 |
| `data/growth_state.json` | **[禁止修改]** | 成长状态 |
| `data/audit/` | **[禁止修改]** | 审计日志 |
| `data/runtime_state.json` | **[禁止修改]** | Runtime 运行时状态 |
| `data/emotion_state.json` | **[禁止修改]** | 情绪状态 |
| `data/relationship_state.json` | **[禁止修改]** | 关系状态 |
| `data/proposals/**`、`data/llm_failures/**` | **[禁止修改]** | 提案与 LLM 失败记录 |
| `data/audit/audit_logs.json` | **[禁止修改]** | 审计数据 |
| `config.yaml` | **[禁止修改]** | 当前部署配置（含 API Key） |
| `config.yaml.example` / `config.yaml.save` | **[禁止修改]** | 配置模板/快照 |
| `.env`（若存在） | **[禁止修改]** | 部署环境变量 |
| `logs/**` | **[禁止修改]** | 运行日志 |
| `羽依核心/yuyi_core_backup/data/**` | **[禁止同步到本仓库]** | 源端数据，禁止带入当前仓库 |
| `羽依核心/yuyi_core_backup/.env` | **[禁止同步到本仓库]** | 源端环境变量 |
| `羽依核心/yuyi_core_backup/config.yaml` | **[禁止同步到本仓库]** | 源端配置 |

> ⚠️ **本仓库无 `.env` 文件**（已确认），不存在该文件被覆盖的风险。

---

## 2. 第二类：核心架构文件（需逐项 diff，禁止盲目覆盖）

> 处理原则：源 vs 目标同名文件 → 逐项 `diff` → 仅在源端**明确更新**时合并 → 否则保留目标现状。

### 2.1 src/core/（9 个文件）

| 源 | 目标 | 标记 | 处理建议 | 原因 |
|---|---|---|---|---|
| `core/__init__.py` | `src/core/__init__.py` | **[合并-需 diff]** | 逐项对比 | 早期快照可能含不同导出 |
| `core/capability_boundary.py` | `src/core/capability_boundary.py` | **[合并-需 diff]** | 逐项对比 | 能力边界基类 |
| `core/event_bus.py` | `src/core/event_bus.py` | **[合并-需 diff]** | 逐项对比 | 事件总线基线 |
| `core/heartbeat.py` | `src/core/heartbeat.py` | **[合并-需 diff]** | 逐项对比 | 心跳基线 |
| `core/module_interface.py` | `src/core/module_interface.py` | **[合并-需 diff]** | 逐项对比 | 模块接口契约 |
| `core/module_interface.py.backup` | — | **[新增-不复制]** | 跳过 | 备份文件，不应进入仓库 |
| `core/module_loader.py` | `src/core/module_loader.py` | **[合并-需 diff]** | 逐项对比 | 加载器 |
| `core/persona.py` | `src/core/persona.py` | **[合并-需 diff]** | 逐项对比 | 角色定义 |
| `core/self_model.py` | `src/core/self_model.py` | **[合并-需 diff]** | 逐项对比 | SelfModel 早期基线 |
| `core/yuyi_cognitive_core.py` | `src/core/yuyi_cognitive_core.py` | **[合并-需 diff]** | 逐项对比 | 认知核心 |
| `core/yuyi_core.py` | `src/core/yuyi_core.py` | **[合并-需 diff]** | 逐项对比 | 羽依核心入口 |

**高风险说明**：
- `core/yuyi_core.py` 与 `core/yuyi_cognitive_core.py` 是 Runtime 与认知主循环入口，**任一覆盖都可能导致 Personality/Memory/Growth/SelfModel 全部接口签名变化**。
- `core/self_model.py` 是 SelfModel 早期基线，但当前仓库 SelfModel 主体已迁至 `src/personality/self_model*.py`，**`src/core/self_model.py` 可能是历史遗留或转发层**，需 diff 确认。
- ⚠️ 当前仓库已有 `src/core/self_model.py`，**禁止直接以羽依核心旧版本覆盖**。

### 2.2 src/context/（3 个文件，全部同名）

| 源 | 目标 | 标记 | 处理建议 | 原因 |
|---|---|---|---|---|
| `context/__init__.py` | `src/context/__init__.py` | **[合并-需 diff]** | 逐项对比 | 上下文包导出 |
| `context/context_manager.py` | `src/context/context_manager.py` | **[合并-需 diff]** | 逐项对比 | 上下文管理器 |
| `context/time_window.py` | `src/context/time_window.py` | **[合并-需 diff]** | 逐项对比 | 时间窗口工具 |

### 2.3 src/contracts/（5 vs 34）

| 源 | 目标 | 标记 | 处理建议 | 原因 |
|---|---|---|---|---|
| `contracts/audit_schema.py` | `src/contracts/audit_schema.py` | **[合并-需 diff]** | 逐项对比 | 审计 schema |
| `contracts/cognitive_event_types.py` | `src/contracts/cognitive_event_types.py` | **[合并-需 diff]** | 逐项对比 | 认知事件类型 |
| `contracts/event_schema.py` | `src/contracts/event_schema.py` | **[合并-需 diff]** | 逐项对比 | 事件 schema |
| `contracts/growth_schema.py` | `src/contracts/growth_schema.py` | **[合并-需 diff]** | 逐项对比 | 成长 schema |
| `contracts/state_schema.py` | `src/contracts/state_schema.py` | **[合并-需 diff]** | 逐项对比 | 状态 schema |
| — | 其余 29 个 `src/contracts/*.py` | **[保留目标]** | 不复制 | 当前仓库已扩展 |

**高风险说明**：
- `growth_schema.py` 与 Growth 引擎的 Proposal/PCR 流程直接耦合，**任何字段变更都可能破坏已落盘的 `data/growth_state.json` 与 `data/proposals/*.json`**。
- `state_schema.py` 与 Runtime 状态、`data/runtime_state.json` 双向绑定。

### 2.4 src/memory/（15 vs 16）

| 源 | 目标 | 标记 | 处理建议 | 原因 |
|---|---|---|---|---|
| `memory/__init__.py` | `src/memory/__init__.py` | **[合并-需 diff]** | 逐项对比 | 导出 |
| `memory/context_builder.py` | `src/memory/context_builder.py` | **[合并-需 diff]** | 逐项对比 | 上下文构建 |
| `memory/event_memory.py` | `src/memory/event_memory.py` | **[合并-需 diff]** | 逐项对比 | 事件记忆 |
| `memory/identity_memory.py` | `src/memory/identity_memory.py` | **[合并-需 diff]** | 逐项对比 | 身份记忆 |
| `memory/memory_context.py` | `src/memory/memory_context.py` | **[合并-需 diff]** | 逐项对比 | 记忆上下文 |
| `memory/memory_extractor.py` | `src/memory/memory_extractor.py` | **[合并-需 diff]** | 逐项对比 | 记忆提取 |
| `memory/memory_formatter.py` | `src/memory/memory_formatter.py` | **[合并-需 diff]** | 逐项对比 | 记忆格式化 |
| `memory/memory_gate.py` | `src/memory/memory_gate.py` | **[合并-需 diff]** | 逐项对比 | 记忆闸门 |
| `memory/memory_retriever.py` | `src/memory/memory_retriever.py` | **[合并-需 diff]** | 逐项对比 | 记忆检索 |
| `memory/memory_service.py` | `src/memory/memory_service.py` | **[合并-需 diff]** | 逐项对比 | 记忆服务 |
| `memory/memory_store.py` | `src/memory/memory_store.py` | **[合并-需 diff]** | 逐项对比 | 记忆存储 |
| `memory/memory_system.py` | `src/memory/memory_system.py` | **[合并-需 diff]** | 逐项对比 | 记忆系统主入口 |
| `memory/memory_verifier.py` | `src/memory/memory_verifier.py` | **[合并-需 diff]** | 逐项对比 | 记忆校验 |
| `memory/module.py` | `src/memory/module.py` | **[合并-需 diff]** | 逐项对比 | 记忆模块 |
| `memory/vector.py` | `src/memory/vector.py` | **[合并-需 diff]** | 逐项对比 | 向量检索 |
| `memory/vector.py.backup` | — | **[新增-不复制]** | 跳过 | 备份文件 |
| — | `src/memory/memory_consolidation_engine.py` | **[保留目标]** | 不复制 | 当前仓库新增能力 |
| — | `src/memory/memory_relevance_evaluator.py` | **[保留目标]** | 不复制 | 当前仓库新增能力 |

**高风险说明**：
- `memory/memory_store.py` 与 `data/memory.json`、`data/chroma_db/` 强耦合，**字段不一致会导致历史记忆不可读**。
- `memory/vector.py` 的 Embedding 模型/维度变更会导致 `data/chroma_db/` 全部失效。

### 2.5 src/personality/（37 vs 56）

| 源 | 目标 | 标记 | 处理建议 | 原因 |
|---|---|---|---|---|
| `personality/__init__.py` | `src/personality/__init__.py` | **[合并-需 diff]** | 逐项对比 | 导出 |
| `personality/behavior_engine.py` | `src/personality/behavior_engine.py` | **[合并-需 diff]** | 逐项对比 | 行为引擎 |
| `personality/behavior_resolver.py` | `src/personality/behavior_resolver.py` | **[合并-需 diff]** | 逐项对比 | 行为解析 |
| `personality/belief_verifier.py` | `src/personality/belief_verifier.py` | **[合并-需 diff]** | 逐项对比 | 信念校验 |
| `personality/conflict_resolver.py` | `src/personality/conflict_resolver.py` | **[合并-需 diff]** | 逐项对比 | 冲突解决 |
| `personality/core_identity.py` | `src/personality/core_identity.py` | **[合并-需 diff]** | 逐项对比 | 核心身份 |
| `personality/evolution_evaluator.py` | `src/personality/evolution_evaluator.py` | **[合并-需 diff]** | 逐项对比 | 演化评估 |
| `personality/evolution_record.py` | `src/personality/evolution_record.py` | **[合并-需 diff]** | 逐项对比 | 演化记录 |
| `personality/growth_accumulator.py` | `src/personality/growth_accumulator.py` | **[合并-需 diff]** | 逐项对比 | 成长累计 |
| `personality/identity_core.py` | `src/personality/identity_core.py` | **[合并-需 diff]** | 逐项对比 | 身份核心 |
| `personality/identity_resolver.py` | `src/personality/identity_resolver.py` | **[合并-需 diff]** | 逐项对比 | 身份解析 |
| `personality/module.py` | `src/personality/module.py` | **[合并-需 diff]** | 逐项对比 | 人格模块 |
| `personality/personality_adapter.py` | `src/personality/personality_adapter.py` | **[合并-需 diff]** | 逐项对比 | 适配器 |
| `personality/personality_controller.py` | `src/personality/personality_controller.py` | **[合并-需 diff]** | 逐项对比 | 控制器 |
| `personality/personality_evolution.py` | `src/personality/personality_evolution.py` | **[合并-需 diff]** | 逐项对比 | 人格演化 |
| `personality/personality_growth_record.py` | `src/personality/personality_growth_record.py` | **[合并-需 diff]** | 逐项对比 | 成长记录 |
| `personality/personality_history.py` | `src/personality/personality_history.py` | **[合并-需 diff]** | 逐项对比 | 历史 |
| `personality/personality_influence.py` | `src/personality/personality_influence.py` | **[合并-需 diff]** | 逐项对比 | 影响力 |
| `personality/personality_profile.py` | `src/personality/personality_profile.py` | **[合并-需 diff]** | 逐项对比 | 画像 |
| `personality/personality_prompt.py` | `src/personality/personality_prompt.py` | **[合并-需 diff]** | 逐项对比 | Prompt 拼装 |
| `personality/personality_resolver.py` | `src/personality/personality_resolver.py` | **[合并-需 diff]** | 逐项对比 | 解析器 |
| `personality/personality_tension.py` | `src/personality/personality_tension.py` | **[合并-需 diff]** | 逐项对比 | 张力 |
| `personality/personality_vector.py` | `src/personality/personality_vector.py` | **[合并-需 diff]** | 逐项对比 | 人格向量 |
| `personality/reflection_engine.py` | `src/personality/reflection_engine.py` | **[合并-需 diff]** | 逐项对比 | 反思引擎 |
| `personality/reflection_record.py` | `src/personality/reflection_record.py` | **[合并-需 diff]** | 逐项对比 | 反思记录 |
| `personality/relationship_state.py` | `src/personality/relationship_state.py` | **[合并-需 diff]** | 逐项对比 | 关系状态 |
| `personality/self_model.py` | `src/personality/self_model.py` | **[禁止覆盖]** | 保留目标 | SelfModel 核心，受 Phase 6 多次迭代保护 |
| `personality/self_model_builder.py` | `src/personality/self_model_builder.py` | **[禁止覆盖]** | 保留目标 | SelfModel 构建器 |
| `personality/self_model_builder_v3.py` | `src/personality/self_model_builder_v3.py` | **[禁止覆盖]** | 保留目标 | SelfModel v3 构建器 |
| `personality/self_model_context_provider.py` | `src/personality/self_model_context_provider.py` | **[禁止覆盖]** | 保留目标 | SelfModel 上下文 |
| `personality/self_model_store.py` | `src/personality/self_model_store.py` | **[禁止覆盖]** | 保留目标 | SelfModel 存储 |
| `personality/self_model_v3.py` | `src/personality/self_model_v3.py` | **[禁止覆盖]** | 保留目标 | SelfModel v3 主类 |
| `personality/self_narrative_context.py` | `src/personality/self_narrative_context.py` | **[合并-需 diff]** | 逐项对比 | 叙事上下文 |
| `personality/self_narrative_history.py` | `src/personality/self_narrative_history.py` | **[合并-需 diff]** | 逐项对比 | 叙事历史 |
| `personality/signal_decay.py` | `src/personality/signal_decay.py` | **[合并-需 diff]** | 逐项对比 | 信号衰减 |
| `personality/trait_relations.py` | `src/personality/trait_relations.py` | **[合并-需 diff]** | 逐项对比 | 特质关系 |
| `personality/trait_state.py` | `src/personality/trait_state.py` | **[合并-需 diff]** | 逐项对比 | 特质状态 |
| `personality/trait_state_updater.py` | `src/personality/trait_state_updater.py` | **[合并-需 diff]** | 逐项对比 | 特质更新 |
| `personality/traits.py` | `src/personality/traits.py` | **[合并-需 diff]** | 逐项对比 | 特质 |
| `personality/value_system.py` | `src/personality/value_system.py` | **[合并-需 diff]** | 逐项对比 | 价值体系 |
| — | 其余 19 个 `src/personality/*.py`（含 self_model_health / guardian / manager / persistence / retention / runtime_context / snapshot / sync_adapter / updater / self_model_core / self_model_adapter / identity_anchor / identity_continuity / identity_stability_engine / self_belief / self_history / self_reflection / personality_evolution_pipeline / personality_stability_engine） | **[保留目标]** | 不复制 | 当前仓库 Phase 6 新增能力 |

**高风险说明**：
- `self_model*.py` 5 个核心文件 + Phase 6 全部新模块，**直接覆盖会破坏已落盘 `src/storage/self_model.json` 与 `data/emotion_state.json` 中的字段约定**。
- `personality/personality_evolution.py` 与 `personality/personality_growth_record.py` 直接读取/写入 `data/growth_state.json`。
- `personality/relationship_state.py` 与 `data/relationship_state.json` 双向绑定。
- `personality/value_system.py` 改变会影响所有 Prompt 拼装路径，**极易引入回归**。

### 2.6 src/growth/（20 + 5 proposal vs 25 + 5 proposal + 4 sync）

| 源 | 目标 | 标记 | 处理建议 | 原因 |
|---|---|---|---|---|
| `growth/__init__.py` | `src/growth/__init__.py` | **[合并-需 diff]** | 逐项对比 | 导出 |
| `growth/behavior_resolver.py` | `src/growth/behavior_resolver.py` | **[合并-需 diff]** | 逐项对比 | 行为解析 |
| `growth/event_extractor.py` | `src/growth/event_extractor.py` | **[合并-需 diff]** | 逐项对比 | 事件提取 |
| `growth/event_history_matcher.py` | `src/growth/event_history_matcher.py` | **[合并-需 diff]** | 逐项对比 | 历史匹配 |
| `growth/event_history_store.py` | `src/growth/event_history_store.py` | **[合并-需 diff]** | 逐项对比 | 历史存储 |
| `growth/event_identity_resolver.py` | `src/growth/event_identity_resolver.py` | **[合并-需 diff]** | 逐项对比 | 事件身份解析 |
| `growth/event_normalizer.py` | `src/growth/event_normalizer.py` | **[合并-需 diff]** | 逐项对比 | 归一化 |
| `growth/event_validator.py` | `src/growth/event_validator.py` | **[合并-需 diff]** | 逐项对比 | 校验 |
| `growth/growth_engine.py` | `src/growth/growth_engine.py` | **[禁止覆盖]** | 保留目标 | Growth 引擎主入口 |
| `growth/growth_evaluator.py` | `src/growth/growth_evaluator.py` | **[禁止覆盖]** | 保留目标 | 评估器 |
| `growth/growth_loop.py` | `src/growth/growth_loop.py` | **[合并-需 diff]** | 逐项对比 | 循环 |
| `growth/growth_record.py` | `src/growth/growth_record.py` | **[禁止覆盖]** | 保留目标 | 记录（已稳定） |
| `growth/growth_schema.py` | `src/growth/growth_schema.py` | **[禁止覆盖]** | 保留目标 | Schema（影响 data/growth_state.json） |
| `growth/growth_state.py` | `src/growth/growth_state.py` | **[禁止覆盖]** | 保留目标 | 状态机（影响 data/growth_state.json） |
| `growth/meaning_resolver.py` | `src/growth/meaning_resolver.py` | **[合并-需 diff]** | 逐项对比 | 意义解析 |
| `growth/memory_former.py` | `src/growth/memory_former.py` | **[合并-需 diff]** | 逐项对比 | 记忆形成 |
| `growth/pipeline.py` | `src/growth/pipeline.py` | **[禁止覆盖]** | 保留目标 | Pipeline（PCR 流程） |
| `growth/proposal_events.py` | `src/growth/proposal_events.py` | **[合并-需 diff]** | 逐项对比 | 事件 |
| `growth/proposal_manager.py` | `src/growth/proposal_manager.py` | **[禁止覆盖]** | 保留目标 | Proposal 管理（PCR 关键） |
| `growth/proposal_store.py` | `src/growth/proposal_store.py` | **[合并-需 diff]** | 逐项对比 | 提案存储 |
| `growth/schemas.py` | `src/growth/schemas.py` | **[合并-需 diff]** | 逐项对比 | schemas |
| `growth/topic_tracker.py` | `src/growth/topic_tracker.py` | **[合并-需 diff]** | 逐项对比 | 主题追踪 |
| `growth/proposal/*.py` (5) | `src/growth/proposal/*.py` (5) | **[禁止覆盖]** | 保留目标 | Proposal 子包核心 |
| — | `src/growth/approval_manager.py`、`growth_limiter.py`、`lifecycle_manager.py`、`state_machines.py` | **[保留目标]** | 不复制 | 当前仓库新增 |
| — | `src/growth/sync/*.py` (4) | **[保留目标]** | 不复制 | 当前仓库新增 |

**高风险说明**：
- `growth_engine.py` / `proposal_manager.py` / `pipeline.py` / `proposal/*` / `growth_schema.py` / `growth_state.py` / `growth_record.py` 全部属于 **GrowthProposal → PCR 流程**，**禁止任何形式的覆盖或合并**。
- `data/growth_state.json`、`data/growth/proposals/proposals.json`、`data/proposals/growth_proposals.json` 均已落盘，schema 改动会直接破坏。

### 2.7 src/emotion/（源无 vs 目标 24）

| 源 | 目标 | 标记 | 处理建议 | 原因 |
|---|---|---|---|---|
| — | `src/emotion/*` (24 个 .py) | **[保留目标]** | 不复制 | 当前仓库新增能力 |

### 2.8 入口与启动文件

| 源 | 目标 | 标记 | 处理建议 | 原因 |
|---|---|---|---|---|
| — | `api_server.py` | **[禁止覆盖]** | 保留目标 | API 入口，已稳定 |
| — | `main.py` | **[禁止覆盖]** | 保留目标 | 主入口 |
| — | `run_server.py` | **[禁止覆盖]** | 保留目标 | 启动脚本 |
| — | `initiative_sender.py` | **[禁止覆盖]** | 保留目标 | 主动发起器 |
| — | `import_chat.py` | **[禁止覆盖]** | 保留目标 | 历史对话导入 |
| — | `requirements.txt` | **[禁止覆盖]** | 保留目标 | 依赖锁 |

**高风险说明**：
- `api_server.py` 注册了 Admin Blueprint、初始化 Orchestrator、RuntimeBridge、模块加载器，**任一覆盖会破坏整套 HTTP/REST API 契约**。
- `requirements.txt` 的包版本直接影响 `data/chroma_db/` 的兼容性（如 chromadb 版本）。

---

## 3. 第三类：新增模块（羽依核心中不存在，当前仓库已具备）

| 目录 | 标记 | 处理建议 |
|---|---|---|
| `src/admin/` | **[保留目标]** | 当前仓库已具备完整 Admin 后端 |
| `src/audit/` | **[保留目标]** | 当前仓库已具备 |
| `src/control/` | **[保留目标]** | 当前仓库已具备 |
| `src/events/` | **[保留目标]** | 当前仓库已具备 |
| `src/goal/` | **[保留目标]** | 当前仓库已具备 |
| `src/initiative/` | **[保留目标]** | 当前仓库已具备 |
| `src/proactive/` | **[保留目标]** | 当前仓库已具备 |
| `src/reflection/` / `src/reflection_system/` | **[保留目标]** | 当前仓库已具备 |
| `src/remote/` | **[保留目标]** | 当前仓库已具备 |
| `src/runtime/` | **[保留目标]** | 当前仓库已具备完整 Runtime |
| `src/safety/` | **[保留目标]** | 当前仓库已具备 |
| `src/screen/` | **[保留目标]** | 当前仓库已具备 |
| `src/storage/` | **[保留目标]** | 当前仓库已具备 |
| `src/thinking/` | **[保留目标]** | 当前仓库已具备 |
| `src/token_opt/` | **[保留目标]** | 当前仓库已具备 |
| `src/understanding/` | **[保留目标]** | 当前仓库已具备 |
| `src/identity/` | **[保留目标]** | 当前仓库已具备 |
| `src/agreement/` | **[保留目标]** | 当前仓库已具备 |
| `src/live2d/` | **[保留目标]** | 当前仓库已具备 |
| `src/permission/` | **[保留目标]** | 当前仓库已具备 |
| `src/relationship/` | **[保留目标]** | 当前仓库已具备 |
| `src/response/` | **[保留目标]** | 当前仓库已具备 |
| `src/dream/` | **[保留目标]** | 当前仓库已具备 |
| `src/orchestrator.py` / `engine.py` | **[保留目标]** | 当前仓库已具备 |

> 这些模块**不需要从羽依核心迁移**，因为源目录本来就没有。

---

## 4. 第四类：前端管理面板

| 项 | 标记 | 处理建议 | 原因 |
|---|---|---|---|
| `static/admin/index.html` | **[保留目标]** | 不复制 | 当前仓库已具备 |
| `static/admin/app.js` | **[保留目标]** | 不复制 | 当前仓库已具备 |
| `static/admin/selfmodel_*.js` | **[保留目标]** | 不复制 | SelfModel Dashboard/Timeline/Health/Retention 全部已具备 |
| `static/admin/css/*` | **[保留目标]** | 不复制 | 全部样式已具备 |
| `static/admin/icons/*` / `static/admin/themes/*` | **[保留目标]** | 不复制 | 资源已具备 |
| `static/admin/js/{dashboard,runtime_dashboard,governance_dashboard,toast,theme-manager,yuyi-*}.js` | **[保留目标]** | 不复制 | 全部已具备 |

> 羽依核心目录**不包含** `static/`，所以前端面板无任何迁移任务。

---

## 5. 第五类：测试文件

| 项 | 标记 | 处理建议 |
|---|---|---|
| `tests/test_admin_*.py` (~10 个) | **[保留目标]** | 不复制 |
| `tests/test_self_model_*.py` | **[保留目标]** | 不复制 |
| `tests/test_growth_*.py` / `test_proposal_*.py` | **[保留目标]** | 不复制 |
| `tests/test_personality_*.py` | **[保留目标]** | 不复制 |
| `tests/test_emotion_*.py` | **[保留目标]** | 不复制 |
| `tests/test_runtime_*.py` | **[保留目标]** | 不复制 |
| `tests/test_phase_*.py` | **[保留目标]** | 不复制 |
| `tests/safety/*` | **[保留目标]** | 不复制 |

> 羽依核心目录**不包含** `tests/`，所以测试无任何迁移任务。

---

## 6. 备份文件清单（禁止复制到当前仓库）

| 源 | 标记 | 处理建议 |
|---|---|---|
| `羽依核心/yuyi_core_backup/core/module_interface.py.backup` | **[跳过]** | 备份文件不入仓 |
| `羽依核心/yuyi_core_backup/memory/vector.py.backup` | **[跳过]** | 备份文件不入仓 |
| `羽依核心/yuyi_core_backup/data/chroma_db.backup_20260728_175800/` | **[跳过]** | chroma_db 备份，不入仓 |

---

## 7. 数据保护复核

| 保护对象 | 当前状态 | 风险 |
|---|---|---|
| `data/memory.json` | 当前仓库存在 | 无风险（不复制源端 data/） |
| `data/chroma_db/` | 当前仓库存在 | 无风险 |
| `data/growth_state.json` | 当前仓库存在 | 无风险 |
| `data/audit/` | 当前仓库存在 | 无风险 |
| `data/emotion_state.json` | 当前仓库存在 | 无风险 |
| `data/relationship_state.json` | 当前仓库存在 | 无风险 |
| `data/runtime_state.json` | 当前仓库存在 | 无风险 |
| `data/proposals/` | 当前仓库存在 | 无风险 |
| `data/llm_failures/` | 当前仓库存在 | 无风险 |
| `data/cog_test_*.json` (24 个) | 当前仓库存在（测试残留） | **可选清理**，但非破坏性，不在迁移范围内 |
| `config.yaml` | 当前仓库存在 | 无风险（不复制源端 config.yaml） |
| `.env` | **当前仓库不存在** | 无风险（即使源端有 .env 也不复制） |
| `logs/**` | 当前仓库存在 | 无风险 |

---

## 8. 总结：迁移任务列表

### 8.1 真正需要"从羽依核心迁移到当前仓库"的文件

**结论：几乎为零。** 当前仓库是羽依核心的**后续演进版本**，所有同名文件目标版本均为更新或同等状态。

仅以下场景存在"合并价值"，但**全部需要逐项 diff 人工确认**：

1. **`src/core/self_model.py`**：羽依核心有早期基线版，当前仓库有新版。**禁止覆盖**（受 SelfModel 权威保护）。
2. **`src/core/module_interface.py`**：接口契约基线，需 diff 确认向后兼容性。
3. **`src/core/yuyi_core.py` / `yuyi_cognitive_core.py`**：入口基线，需 diff。
4. **`src/contracts/*` 的 5 个 schema 文件**：schema 兼容性问题，会影响落盘数据兼容性。
5. **`src/growth/*`（除已标 [禁止覆盖]）**：逐项 diff，但**禁止覆盖 PCR 关键文件**。
6. **`src/personality/*`（除已标 [禁止覆盖]）**：逐项 diff，但**禁止覆盖 SelfModel 5 个核心 + value_system + relationship_state**。
7. **`src/memory/*`（全部）**：逐项 diff，注意 `memory_store.py` / `vector.py` 与 `data/` 强耦合。

### 8.2 真正需要"从当前仓库拉回羽依核心"的文件

**这是反向迁移**（如果用户希望把开发版同步到羽依核心）：

- `src/personality/self_model_*.py`（Phase 6 新增的 12+ 模块）
- `src/emotion/*`（24 个文件）
- `src/runtime/*`（完整 Runtime）
- `src/admin/*`（Admin 完整后端）
- `src/growth/sync/*`（4 个 sync 模块）
- `src/growth/{approval_manager,growth_limiter,lifecycle_manager,state_machines}.py`
- `src/memory/{memory_consolidation_engine,memory_relevance_evaluator}.py`
- `src/contracts/*` 的 29 个新增 schema
- `static/admin/*` 全部
- `tests/*` 全部

### 8.3 不需要迁移的文件

- 羽依核心的所有 `.backup` 文件
- 羽依核心的 `data/`、`config.yaml`、`.env`
- 任何备份目录（`*.backup_YYYYMMDD_HHMMSS`）

---

## 9. 风险等级总览

| 风险等级 | 文件 | 数量 |
|---|---|---|
| 🔴 极高风险（覆盖会破坏数据/API） | `data/*`、`config.yaml`、`.env`、`logs/*`、`growth/{growth_engine,proposal_manager,pipeline,growth_schema,growth_state,growth_record,proposal/*}.py`、`personality/{self_model*,self_narrative*,value_system,relationship_state}.py`、`core/{yuyi_core,yuyi_cognitive_core,module_interface,module_loader}.py`、`memory/{memory_store,vector}.py`、`api_server.py`、`requirements.txt` | ~30 |
| 🟠 高风险（覆盖会引入回归） | `core/{capability_boundary,event_bus,heartbeat,persona,self_model}.py`、`personality/*`（除极高风险外）、`growth/*`（除极高风险外）、`memory/*`（除极高风险外）、`contracts/*` | ~80 |
| 🟡 中风险（需 diff 后决定） | `context/*` | 3 |
| 🟢 低风险（不存在或已就绪） | 羽依核心缺失的新增模块、Admin 前端、测试 | 数百 |

---

## 10. 等用户确认

**当前状态：扫描完成，差异报告已生成，未做任何修改。**

请确认下一步：

1. **你希望的目标方向是？**
   - A. **羽依核心 → 当前仓库**（从早期快照往开发版合并）：几乎无实际任务，仅个别文件需 diff
   - B. **当前仓库 → 羽依核心**（反向同步，把开发版推到羽依核心）
   - C. **双向互不打扰**（仅生成报告，不实际移动文件）

2. **如果选 A，请指明**：
   - 哪些 B 类文件需要我执行 `diff` 并输出对比结果？
   - 是否接受"全 B 类文件保留目标现状，不做任何合并"的安全结论？

3. **是否需要我执行 Step 5（`python -m pytest tests`）？**
   - 该操作**只读不写**，不会触动 data/、config.yaml、.env

请审阅并指示。
