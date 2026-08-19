# Phase 4.1 Runtime 生命循环接管 · 架构影响报告

> 日期：2026-08-12
> 章程流程：审计 → 最小修改 → 测试 → 报告（本报告为最终步）
> 约束遵守：未重写 Identity/Memory/Growth/Personality/Emotion 架构；未新建任何 Memory/Personality/Reflection/Runtime 系统

---

## 1. 六个目标 Stage 的最终状态

| Stage | P4 审计状态 | 本轮动作 | 现状 |
|-------|------------|---------|------|
| 2 Memory Retrieval | 🟡 部分有效 | **修复 3 处接线错误**（见 §2） | ✅ 已有效（含多用户隔离） |
| 3 Emotion Update | ✅ 已有效 | 复核：EmotionManager 真实构造、update_from_event+快照入 ctx | ✅ 已有效（无需改动） |
| 5 Personality Update | ✅ 已有效 | 复核：PersonalityResolver 优先 + adapter 快照降级 | ✅ 已有效（无需改动） |
| 6 Personality Context | ✅ 已有效 | 复核：人格→自然语言 + IdentityContextBuilder（Identity⊥Personality 边界） | ✅ 已有效（无需改动） |
| 9 SelfModel Build | ⚪ no-op | **接线**：调既有 refresh_self_model_from_runtime + 快照入 ctx | ✅ 已有效 |
| 14 Response Generation | 🟡 部分有效 | **接线**：self_model_context 注入 adapter 与 engine 双路径 | ✅ 已有效 |

## 2. 修改内容（2 个文件，最小改动）

### `src/runtime/runtime_core.py`
1. **Stage 9**（原 no-op）→ 调用 init 阶段已真实构造的 `self_model_manager`：先 `refresh_self_model_from_runtime()` 刷新，再 `get_self_model_full()` 只读快照写入 `ctx.self_model_snapshot`。全程 fail-soft。
2. **Stage 14** → `req_kwargs` 新增 `self_model_context`（ResponseAdapter 路径）；engine 降级路径的 `self_model_context={}` 硬编码改为读 Stage 9 快照。
3. **Stage 2 修正三处历史接线错误**：
   - 旧代码 `ma.retrieve(ctx)` 把 ctx 对象当 query（检索词=对象 repr，检索必空转）→ 改为传 `ctx.user_message`；
   - 旧代码丢弃 retrieve 返回值（`ctx.retrieved_memories` 从未写入，adapter 路径等于无检索）→ 结果写入 ctx；
   - 新增 `_extract_event_user_id()`（event.payload → ctx.inputs 双源），全链路传 user_id；store 降级路径改用 `get_by_user` 过滤。

### `src/runtime/adapters/impl/memory_runtime_adapter.py`
4. `retrieve/_retrieve` 新增可选 `user_id`（不传回落构造默认值，零破坏）；vector 路径传 `user_id`（对旧签名 vector 实现 TypeError 回退兼容）；store 降级路径按 user_id 严格过滤（与 P3 口径一致）。

## 3. 原因

- Stage 9/14 是章程点名的接线目标，且资产（SelfModelManager/Updater/Bootstrap）在 `__init__` 已就位——缺口仅在阶段方法为空，典型「连接不完整」。
- Stage 2 的三处错误使 Runtime 路径的记忆检索**事实上从未生效**（记忆连续性断点），不修复则「Runtime 驱动」名不副实。

## 4. 测试结果

stub 第三方依赖后加载**真实** `runtime_core.py` / `memory_runtime_adapter.py` 裸实例测试：

- Stage 9/14：**9/9 通过**（快照入 ctx / refresh 真实触发 / 无 manager 安全 no-op / control_blocked 跳过 / adapter 与 engine 双路径收到 self_model_context / identity_context 保持）
- Stage 2：**7/7 通过**（真实 query 修正 / 双源 user_id 提取 / 结果写入 ctx / 旧签名 adapter 兼容 / store 降级按用户过滤截 20 条 / control_blocked 跳过）
- `py_compile`：全部修改文件通过。临时脚本已清理。

**局限**：本机 venv 为 Linux 格式无法运行，完整 pytest 需在服务器补跑（建议 `pytest tests/runtime -x`）。

## 5. 架构影响（对照判断标准）

| 连续性 | 本轮增益 |
|--------|---------|
| 身份连续性 | Stage 6→14 的 identity_context 链路复核确认贯通 |
| 记忆连续性 | **Stage 2 从"事实空转"变为真实检索**，且四轮连续性（写入→检索→注入）均有 user_id 隔离 |
| 人格连续性 | Stage 5/6 复核确认有效；无需改动即符合章程 |
| 成长连续性 | **Stage 9 起 SelfModel 每轮随 Runtime 刷新**——自我认知首次进入生命循环，而非库外自转 |

- 「RuntimeCore 唯一生命周期调度中心」：入口侧已在前期收敛（CLI/API 100% 经 RuntimePipeline）；本轮后 6 个关键 Stage 全部真实工作。orchestrator legacy 兜底按章程「秒级回滚」原则保留，属安全网而非第二中心。
- 未新增代码量净值极低（Stage 9/14 为调用既有资产）；下一步建议：Stage 10-13（SelfModel Evolution/Reflection/Validation/Persistence）按同一模式逐个接线，或服务器侧灰度验证 Stage 14 真实出稿率。
