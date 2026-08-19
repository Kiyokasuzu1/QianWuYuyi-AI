# Phase 4.0.2-P2 Prompt 链路统一 · 前期分析报告

> 日期：2026-08-11
> 性质：纯分析，未修改任何代码（章程：「先分析现有路径，不要直接重构」）
> 前置：P1 已完成硬编码身份 → IDENTITY_CORE 统一

---

## 1. Prompt 构造入口清单（4 个）

| # | 入口 | 构造方式 | 身份来源（P1 后） |
|---|------|---------|------------------|
| E1 | `src/engine.py` `ResponseEngine.generate()` | 内部 `system_parts` 逐段拼接（`_build_identity_prompt()` + personality + memory + emotion + relationship…） | ✅ IDENTITY_CORE |
| E2 | `src/response/prompt_builder.py` `PromptBuilder.build_messages()`（由 `src/response/engine.py` 的**第二个 ResponseEngine** 封装） | `build_messages()` 参数化组装（identity_context 由调用方注入） | ✅ IDENTITY_CORE（P1 修复） |
| E3 | `src/core/yuyi_core.py` `YuyiCore._build_system_prompt()` | 单一大 f-string（规则 + persona_base + 记忆 + 时间） | ✅ IDENTITY_CORE（P1 修复名称） |
| E4 | `src/response_phase4/prompt_renderer.py` `PromptRenderer.build()` | 结构化 RenderedPrompt（identity_summary / self_description / memory_section / relationship_section 分区） | identity_summary 数据驱动（非 IDENTITY_CORE 直读） |

另有两个同名类注意点：`src/engine.py:ResponseEngine` 与 `src/response/engine.py:ResponseEngine` **同名不同实现**，是历史叠层。

## 2. 生产调用链（api_server `/v1/chat/completions` 实测追踪）

当前配置：`phase4_enabled=false`、`runtime.enabled=true`、`adapters_enabled=true`

```
/v1/chat/completions
 ├─ [关闭] RuntimeController → E4 PromptRenderer → DeepSeekAdapter   ← phase4_enabled=false
 ├─ [主用] RuntimePipeline(runtime=RuntimeCore shared impl)
 │    ├─ runtime 路径: RuntimeCore → AdapterRegistry → response_adapter_impl → E2   ← 条件触发
 │    └─ fallback:  Orchestrator._generate_reply → legacy_generate → E1              ← 常见路径
 └─ [兜底] Orchestrator.process() → E1
```

结论：**线上回复的 Prompt 实际由 E1（`src/engine.py`）产出**；E2 仅在 RuntimeCore 的 response adapter 被真正调度时参与；E4 完整但未启用；E3 不在服务器链路上。

## 3. 死活路径判定

| 入口 | 状态 | 证据 |
|------|------|------|
| E1 `src/engine.py` | 🟢 活（主链路） | `orchestrator.py:278` 实例化；`legacy_generate()`（orchestrator.py:1029）与 `_process`（:1834）均调用 |
| E2 `response/prompt_builder` | 🟡 条件活 | 唯一调用方 `runtime/adapters/impl/response_adapter_impl.py:77`；runtime.py:469 注册了 response_adapter，但是否在每轮真实生成回复取决于 RuntimeCore 阶段启用度（阶段四审计对象） |
| E3 `yuyi_core` | 🔴 遗留（非服务器路径） | 仅 `yuyi.py:177`、`src/terminal_chat.py:15` 两个旧 CLI 入口使用；`main.py`/`api_server.py` 均不引用 |
| E4 `response_phase4` | ⚪ 建成未启用 | `runtime_controller.py:36,146` 接入，但 `config.yaml: phase4_enabled=false` |

## 4. 与章程「统一 Prompt 系统」的差距

1. **E1 vs E2 能力重叠**：两者都组装 personality/memory/emotion/relationship，字段口径各自演化（E2 有 agreement/experience 分区，E1 是平铺拼接），长期会再次漂移。
2. **E4 与 E1/E2 数据模型不同**：E4 用结构化 RenderedPrompt 分区（最接近章程目标形态），但身份数据来自 `identity_summary` 快照而非 IDENTITY_CORE 直读——启用前需验证快照来源一致性。
3. **E3 是简化版影子实现**：规则串 + persona docs 直读，缺少 Growth/SelfModel 注入，继续存在会让 CLI 行为与服务器行为不一致。

## 5. 统一建议（供决策，本阶段不动代码）

- **目标形态**：以 E4 的 RenderedPrompt 分区模型为终点（与章程「SYSTEM PROMPT + Identity + … 分区」结构最吻合），但启用前必须完成阶段四 Runtime 审计与 E4 灰度验证（章程：禁止启用未经验证的 Phase4 Prompt 路径）。
- **过渡纪律**：E1 保持线上唯一权威；E2 不动（等阶段四确认 response adapter 真实调度情况）；E3 标记 deprecated 但不删（章程：禁止删除大量模块）。
- **禁止项复述**：不新建第 5 个 PromptBuilder。

## 6. 架构影响

无代码变更。本报告为阶段二「Prompt 链路统一」的决策依据；E4 启用评估依赖阶段四（Runtime Stage 0-16 审计）结论。
