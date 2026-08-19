# Phase 4.1b SelfModel 生命周期闭环 · 架构影响报告

> 日期：2026-08-12
> 前置：Phase 4.1 已接通 Stage 9（SelfModel Build）
> 约束遵守：未新增 SelfModel 系统；全部使用 src/runtime/self_model/ 既有资产；
> LLM 不直接修改 SelfModel；RuntimeCore 保持唯一生命周期中心；Orchestrator fallback 保留

---

## 1. 接线内容（仅 `src/runtime/runtime_core.py`，4 个 no-op → 真实实现 + 2 个辅助）

| Stage | 原状 | 接线方式（全部为既有资产） |
|-------|------|---------------------------|
| 10 Evolution | no-op | `get_insights()` → `generate_self_model_suggestion_from_insights()` → 去重入队 `_pending_self_model_suggestions`（容量 300 与既有约定一致）。**只生成提案，不应用** |
| 11 Reflection | no-op | `run_scheduled_reflection()`——既有 reflection_scheduler 自带触发条件（非每轮强制反思）且明确「不自动接受 GrowthProposal」 |
| 12 Validation | no-op | `requires_approval=True` 留队等外部审批；`False` 的经 `SelfModelEvolutionPolicy.evaluate`（若注入）判定 allow 后走既有 `accept_self_model_suggestion()` 应用。评估失败一律偏向不应用（fail-safe） |
| 13 Persistence | no-op | 本轮有实际变化时经 `SelfModelAdapter.save_state()`（Phase 6.3 bootstrap 已 attach persistence）落盘；无变化跳过并记录原因 |

辅助（不改变任何语义）：
- `_sm_chain_mark()`：每阶段把结果写入 `ctx.self_model_chain`——验收标准要求的「完整可追踪链路」载体
- `_suggestion_to_change_dicts()`：提案字段 → 策略评估 changes 格式（field/key 约定对齐）

## 2. 约束逐条核对

| 约束 | 落实 |
|------|------|
| 1. 不新增 SelfModel 系统 | ✅ 仅调用既有 manager/updater/scheduler/policy/adapter |
| 2. 使用 src/runtime/self_model/ 资产 | ✅ policy 来自 self_model_policy.py；persistence 经 bootstrap 装配的 adapter |
| 3. LLM 不直接修改 SelfModel | ✅ Stage 10 只产出 Suggestion；应用必须经 Stage 12 验证/审批（G9 测试锁定） |
| 4. Event→GrowthProposal→Validation→Persistence | ✅ 即 Stage 10→12→13，链标记可追踪（G16） |
| 5. RuntimeCore 唯一生命周期中心 | ✅ 仅改 stage 方法，未动调度器/壳层/orchestrator |
| 6. Orchestrator 保留 fallback | ✅ 未触碰 |

## 3. 测试结果（16/16 通过）

stub 第三方依赖后加载真实 `runtime_core.py` 裸实例测试：

- Stage 10：建议生成入队 / changed 标记 / 链标记 / **同 ID 去重**（G1-G4）
- Stage 11：调度器 skip 路径记录（G5）
- Stage 12：免审批应用 / 应用后出队 / **需审批留队** / **policy deny 不应用** / policy allow 应用 / changes 格式（G6-G12）
- Stage 13：有变化落盘 / 无变化跳过记原因（G13-G15）
- 整链：四阶段标记齐全（G16）

`py_compile` 通过；临时脚本已清理。**局限**：完整 pytest 需服务器侧补跑。

## 4. 架构影响（对照判断标准）

| 标准 | 增益 |
|------|------|
| 自我连续性 | Stage 9→13 闭环：自我模型每轮刷新→演化建议→反思→验证→落盘，跨重启经 bootstrap 恢复 |
| 人格连续性 | 人格变化经 PCR→Suggestion→审批链，Stage 12 是最后一道自动闸门 |
| 成长可解释性 | `ctx.self_model_chain` 使每轮四阶段结果可审计；policy 的 reasons/conflicts 提供拒绝理由 |
| 长期稳定性 | 每轮零变化零写盘（no_change 跳过）；fail-soft 全覆盖，单阶段异常不中断生命周期 |

## 5. 后续建议（不在本轮范围）

- 生产注入 `policy_engine`（当前未注入时 Stage 12 对免审批建议直接走 accept，偏宽松；注入后才有 allow/deny 细粒度判定）
- 服务器侧端到端验证一次完整对话的 `self_model_chain` 四段标记
